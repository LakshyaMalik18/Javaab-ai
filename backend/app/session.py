"""
session.py — in-memory session store + DuckDB lifecycle. THE PRIVACY MECHANISM.

Each session owns:
  - an in-memory DuckDB connection (database=":memory:" — never a file path),
  - the cleaned DataFrames, the schema contract, and per-session guardrail metrics,
  - an optional user-supplied LLM key, held ONLY in memory for the session.

On close (explicit /session DELETE, idle timeout, or store shutdown) `wipe()`
runs deterministically: tables are unregistered, the DuckDB connection is closed,
every user-derived object is dropped, and the key reference is cleared. Nothing
user-derived is ever written to disk; the guardrail metrics that survive contain
query metadata only (allowed/blocked/reason/tables), never user data.
"""
from __future__ import annotations

import time
import uuid
from typing import Callable

import duckdb
import pandas as pd

from app.engines import guardrail as guardrail_mod
from app.engines import orchestrator
from app.engines import schema_contract as contract_mod
from app.llm import FallbackProvider, GeminiProvider, GroqProvider, get_provider
from app.llm.base import LLMConfigError, LLMProvider
from app.models import AnswerResult, SchemaContract
from app.upload_pipeline import UploadResult, rebuild_from_raw, validate_rules

#: in-memory marker — sessions NEVER touch a disk file.
IN_MEMORY = ":memory:"
DEFAULT_TIMEOUT_SECONDS = 30 * 60


class SessionError(Exception):
    """Base for session-layer errors the API maps to HTTP codes."""


class SessionNotFound(SessionError):
    pass


class SessionClosed(SessionError):
    pass


ProviderFactory = Callable[..., LLMProvider]


def _default_provider_factory(*, privacy_mode: bool = False, user_key: str | None = None) -> LLMProvider:
    """Build the session's provider, enforcing the strict privacy rule.

    Privacy Mode → bare Groq, NEVER wrapped in a fallback (the user opted out of
    other providers; their data must never be silently routed elsewhere).

    Default mode → Gemini primary with an automatic Groq fallback on rate-limit.
    The Groq fallback uses the server key (api_key=None), never the user's Gemini
    key."""
    if privacy_mode:
        return get_provider(privacy_mode=True, user_key=user_key)
    return FallbackProvider(
        primary=GeminiProvider(api_key=user_key),
        fallback=GroqProvider(api_key=None),
    )


def manual_join_reset_warning(labels: list[str], action: str) -> list[str]:
    """Build the user-facing warning that a rebuild discarded manual joins. Empty list
    when there were none to lose (so no warning is shown). `action` is the trigger,
    e.g. 'Re-uploading' / 'Applying a cleaning rule'."""
    if not labels:
        return []
    n = len(labels)
    plural = "s" if n > 1 else ""
    return [
        f"{action} reset {n} manually-defined join{plural} ({'; '.join(labels)}). "
        "Redefine them on the Relationships step."
    ]


class Session:
    """One user session. Owns its data and its in-memory DuckDB connection."""

    def __init__(
        self,
        session_id: str,
        provider: LLMProvider,
        *,
        privacy_mode: bool = False,
        user_key: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.id = session_id
        self.provider = provider
        self.privacy_mode = privacy_mode
        self._user_key = user_key  # in memory ONLY — never persisted, never logged
        self.timeout_seconds = timeout_seconds
        self.created_at = time.time()
        self.last_active = self.created_at

        # the privacy mechanism: a private, in-memory DuckDB for this session only
        self.db_location = IN_MEMORY
        self._con = duckdb.connect(database=IN_MEMORY)
        self._closed = False

        # user-derived state (all wiped on close)
        self.tables: dict[str, pd.DataFrame] = {}
        # the RAW, pre-clean frames — retained IN MEMORY ONLY so custom cleaning rules
        # can re-run the engine without a re-upload. Wiped on close like everything else.
        self.raw_tables: dict[str, pd.DataFrame] = {}
        # custom cleaning rules added this session (persist + re-apply on each re-run)
        self.cleaning_rules: list[dict] = []
        self.profiles: dict[str, dict] = {}
        self.relationships: list[dict] = []
        self.ledger: list[dict] = []
        self.flags: list[dict] = []
        self.table_meta: list[dict] = []
        self.errors: list[str] = []
        self.contract: SchemaContract | None = None

        # session-scoped metrics (metadata only — safe to keep)
        self.metrics = guardrail_mod.GuardrailMetrics()
        self.queries_total = 0
        self.queries_answered = 0

    # ── lifecycle ──────────────────────────────────────────────────────────────

    @property
    def closed(self) -> bool:
        return self._closed

    def touch(self) -> None:
        self.last_active = time.time()

    def is_expired(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return (now - self.last_active) > self.timeout_seconds

    def _ensure_open(self) -> None:
        if self._closed:
            raise SessionClosed(f"session {self.id} has been wiped")

    def wipe(self) -> None:
        """Destroy everything user-derived. Idempotent. After this call no
        uploaded data, contract, or key survives, and the DuckDB connection is
        closed (its in-memory pages freed)."""
        if self._closed:
            return
        self._closed = True
        try:
            for name in list(self.tables):
                try:
                    self._con.unregister(name)
                except Exception:
                    pass
            self._con.close()
        finally:
            self.tables.clear()
            self.raw_tables.clear()       # the retained raw frames die with the session
            self.cleaning_rules.clear()
            self.profiles.clear()
            self.relationships.clear()
            self.ledger.clear()
            self.flags.clear()
            self.table_meta.clear()
            self.errors.clear()
            self.contract = None
            self._user_key = None

    # ── manual-join loss detection (notification only) ───────────────────────────

    def manual_join_labels(self) -> list[str]:
        """User-defined joins that exist ONLY in the cached contract — i.e. edges not
        among the discovered relationships. These are silently lost on any contract
        rebuild (re-upload / apply-rules), so callers surface a warning before rebuild.
        Returns [] when there's no contract or no manual joins (nothing to warn about).

        v1 does NOT preserve these across a rebuild (that's a v1.1 item) — this only
        makes the loss non-silent. Comparison is undirected so a discovered edge whose
        orientation differs is never mistaken for a manual one (avoids false warnings)."""
        if self.contract is None:
            return []
        discovered: set[frozenset] = set()
        for e in self.relationships:
            if isinstance(e, dict):
                ft, fc, tt, tc = e["from_table"], e["from_col"], e["to_table"], e["to_col"]
            else:
                ft, fc, tt, tc = e.from_table, e.from_col, e.to_table, e.to_col
            discovered.add(frozenset({(ft, fc), (tt, tc)}))

        manual: list[str] = []
        for e in self.contract.relationships:
            pair = frozenset({(e.from_table, e.from_col), (e.to_table, e.to_col)})
            if pair not in discovered:
                manual.append(f"{e.from_table}.{e.from_col} → {e.to_table}.{e.to_col}")
        return manual

    # ── data loading ─────────────────────────────────────────────────────────────

    def load_upload(self, up: UploadResult) -> None:
        """Adopt the result of upload_pipeline.process_upload into the session and
        register the cleaned tables into this session's DuckDB."""
        self._ensure_open()
        self.tables.update(up.tables)
        self.raw_tables.update(up.raw_tables)
        self.profiles.update(up.profiles)
        self.relationships = up.relationships
        self.ledger.extend(up.ledger)
        self.flags.extend(up.flags)
        self.table_meta.extend(up.table_meta)
        self.errors.extend(up.errors)
        # a fresh upload invalidates any previously-built contract
        self.contract = None
        for name, df in up.tables.items():
            self._con.register(name, df)

    # ── duplicate resolution (explicit, user-driven — never auto-removes) ─────────

    def remove_duplicate_rows(self, decisions: list[dict]) -> dict:
        """Drop the duplicate rows the user explicitly chose to remove and re-point
        this session's DuckDB at the trimmed frames, so every later query sees the
        removal. SAFE BY DESIGN: only a decision with action=='remove' drops rows;
        for each such group the first reported index is KEPT (the representative) and
        the rest are dropped. 'keep' decisions and unmentioned groups change nothing —
        nothing is ever auto-removed.

        `row_indices` are treated as positional offsets into the table (matching how
        the upload flags report exact groups and near pairs). Returns
        {removed_rows, tables: <updated table_meta>}."""
        self._ensure_open()

        # collect the positional rows to drop, per table (keep each group's first)
        drop_by_table: dict[str, set[int]] = {}
        for d in decisions:
            if d.get("action") != "remove":
                continue
            table = d.get("table")
            idxs = [int(i) for i in (d.get("row_indices") or [])]
            if table not in self.tables or len(idxs) < 2:
                continue
            drop_by_table.setdefault(table, set()).update(idxs[1:])

        removed_total = 0
        for table, positions in drop_by_table.items():
            df = self.tables[table]
            n = len(df)
            valid = sorted(p for p in positions if 0 <= p < n)
            if not valid:
                continue
            trimmed = df.drop(df.index[valid]).reset_index(drop=True)
            removed_total += n - len(trimmed)
            self.tables[table] = trimmed
            # re-point DuckDB at the trimmed frame so queries reflect the removal
            try:
                self._con.unregister(table)
            except Exception:
                pass
            self._con.register(table, trimmed)
            for meta in self.table_meta:
                if meta.get("name") == table:
                    meta["row_count"] = int(len(trimmed))

        # the resolved duplicate flags no longer describe the data — drop them for any
        # table we modified so the report can't keep offering already-removed rows.
        # (Column meanings/types/joins are unchanged by dropping rows, so the cached
        # contract stays valid and we avoid a needless re-label LLM call.)
        if drop_by_table:
            self.flags = [
                f for f in self.flags
                if not (
                    f.get("kind") in ("exact_duplicate", "near_duplicate")
                    and f.get("table") in drop_by_table
                )
            ]

        return {"removed_rows": removed_total, "tables": self.table_meta}

    # ── custom cleaning rules (re-run cleaning from the retained raw frames) ──────

    def apply_cleaning_rules(self, new_rules: list[dict]) -> dict:
        """Validate and apply custom cleaning rules, re-running the cleaning engine
        over the RETAINED RAW frames (never a re-upload) and re-pointing DuckDB at the
        freshly-cleaned tables. Rules accumulate per session and the FULL set is
        re-applied each call, so the result is deterministic regardless of call order.

        NOTE — interaction with remove_duplicate_rows(): re-cleaning rebuilds each
        table from raw, so any rows previously dropped via Remove-dupe REAPPEAR and the
        duplicates are re-detected (re-flagged) for the user to act on again. Rule
        application is a from-raw rebuild; row-level dedup is a separate edit on the
        cleaned set and is intentionally not replayed here.

        Raises SessionError (→ 400) on an invalid rule; nothing is applied in that case."""
        self._ensure_open()
        if not self.raw_tables:
            raise SessionError("no data uploaded yet")
        try:
            validated = validate_rules(new_rules, self.raw_tables)
        except ValueError as e:
            raise SessionError(str(e))

        # accumulate, then re-apply the FULL rule set from raw
        # a re-clean rebuilds the contract → any manual joins on it are lost; capture
        # them now (before the rebuild) so we can warn rather than drop them silently.
        lost_manual = manual_join_reset_warning(
            self.manual_join_labels(), "Applying a cleaning rule"
        )

        self.cleaning_rules = self.cleaning_rules + validated
        rebuilt = rebuild_from_raw(self.raw_tables, self.cleaning_rules)

        # swap the cleaned state in wholesale (raw_tables holds the complete set)
        for name in list(self.tables):
            try:
                self._con.unregister(name)
            except Exception:
                pass
        self.tables = dict(rebuilt.tables)
        self.profiles = dict(rebuilt.profiles)
        self.relationships = list(rebuilt.relationships)
        self.ledger = list(rebuilt.ledger)
        self.flags = list(rebuilt.flags)
        self.table_meta = list(rebuilt.table_meta)
        # data changed → the cached contract (and any manual joins on it) is rebuilt
        self.contract = None
        for name, df in self.tables.items():
            self._con.register(name, df)

        return {
            "tables": self.table_meta,
            "ledger": {
                "total_cells_affected": sum(r["cells_affected"] for r in self.ledger),
                "records": self.ledger,
            },
            "flags": self.flags,
            "errors": rebuilt.errors,
            "warnings": lost_manual,
            "rules_applied": len(self.cleaning_rules),
        }

    # ── schema contract (built once, cached) ─────────────────────────────────────

    def ensure_contract(self) -> SchemaContract:
        """Build the confidence-scored contract once and cache it. Falls back to a
        deterministic contract if no LLM key is configured (still usable). Rate
        limits are NOT swallowed here — they propagate so the API returns 429."""
        self._ensure_open()
        if self.contract is not None:
            return self.contract
        if not self.tables:
            raise SessionError("no data uploaded yet")
        try:
            self.contract = contract_mod.build_contract(
                self.tables, self.provider,
                flags=self.flags, profiles=self.profiles, relationships=self.relationships,
            )
        except LLMConfigError:
            # no key → deterministic contract, marked by lower confidences
            self.contract = contract_mod.build_contract(
                self.tables, self.provider,
                flags=self.flags, profiles=self.profiles, relationships=self.relationships,
                skip_llm=True,
            )
        return self.contract

    # ── ask ──────────────────────────────────────────────────────────────────────

    def ask(self, question: str, *, max_rows: int = 500) -> AnswerResult:
        """Answer one question against this session's data, running the validated
        SQL inside this session's own in-memory DuckDB connection."""
        self._ensure_open()
        # reset the per-request fallback flag before any LLM call this request makes
        if isinstance(self.provider, FallbackProvider):
            self.provider.reset_fallback()
        contract = self.ensure_contract()
        self.queries_total += 1
        res = orchestrator.answer(
            question,
            self.tables,
            contract=contract,
            provider=self.provider,
            metrics=self.metrics,
            max_rows=max_rows,
            con=self._con,
        )
        # if the default-mode primary was rate-limited, the answer came from Groq —
        # surface a small, honest note (privacy mode never reaches this branch).
        if isinstance(self.provider, FallbackProvider) and self.provider.used_fallback:
            res.provider_used = self.provider.fallback.name
            res.fallback_note = (
                f"The default model was busy, so this answer was generated by the "
                f"{self.provider.fallback.name} fallback model."
            )
        if res.status == "answered":
            self.queries_answered += 1
        return res


class SessionStore:
    """Process-wide registry of live sessions + a lifetime metrics accumulator
    (metadata only) so the trust panel survives individual session wipes."""

    def __init__(self, provider_factory: ProviderFactory | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self.provider_factory: ProviderFactory = provider_factory or _default_provider_factory
        self.sessions_created = 0
        self._lifetime_allowed = 0
        self._lifetime_blocked = 0
        self._lifetime_answered = 0

    def create(
        self,
        *,
        privacy_mode: bool = False,
        user_key: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> Session:
        self.sweep()
        sid = uuid.uuid4().hex
        provider = self.provider_factory(privacy_mode=privacy_mode, user_key=user_key)
        sess = Session(
            sid, provider,
            privacy_mode=privacy_mode, user_key=user_key, timeout_seconds=timeout_seconds,
        )
        self._sessions[sid] = sess
        self.sessions_created += 1
        return sess

    def get(self, session_id: str | None) -> Session:
        if not session_id or session_id not in self._sessions:
            raise SessionNotFound(f"unknown or expired session: {session_id}")
        sess = self._sessions[session_id]
        if sess.is_expired():
            self.close(session_id)
            raise SessionNotFound(f"session expired: {session_id}")
        sess.touch()
        return sess

    def close(self, session_id: str) -> bool:
        sess = self._sessions.pop(session_id, None)
        if sess is None:
            return False
        self._retire(sess)
        sess.wipe()
        return True

    def sweep(self, now: float | None = None) -> int:
        """Close every idle/expired session. Returns how many were wiped."""
        now = now if now is not None else time.time()
        expired = [sid for sid, s in self._sessions.items() if s.is_expired(now)]
        for sid in expired:
            self.close(sid)
        return len(expired)

    def close_all(self) -> None:
        for sid in list(self._sessions):
            self.close(sid)

    def _retire(self, sess: Session) -> None:
        self._lifetime_allowed += sess.metrics.allowed
        self._lifetime_blocked += sess.metrics.blocked
        self._lifetime_answered += sess.queries_answered

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def aggregate(self) -> dict:
        """Lifetime totals: retired sessions + currently-live ones (no overlap)."""
        allowed = self._lifetime_allowed
        blocked = self._lifetime_blocked
        answered = self._lifetime_answered
        for s in self._sessions.values():
            allowed += s.metrics.allowed
            blocked += s.metrics.blocked
            answered += s.queries_answered
        total = allowed + blocked
        return {
            "queries_answered": answered,
            "guardrail_allowed": allowed,
            "guardrail_blocked": blocked,
            "guardrail_total": total,
            "destructive_blocked_pct": (blocked / total * 100.0) if total else 100.0,
            "sessions_created": self.sessions_created,
            "active_sessions": self.active_count,
        }
