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
from app.upload_pipeline import UploadResult

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
            self.profiles.clear()
            self.relationships.clear()
            self.ledger.clear()
            self.flags.clear()
            self.table_meta.clear()
            self.errors.clear()
            self.contract = None
            self._user_key = None

    # ── data loading ─────────────────────────────────────────────────────────────

    def load_upload(self, up: UploadResult) -> None:
        """Adopt the result of upload_pipeline.process_upload into the session and
        register the cleaned tables into this session's DuckDB."""
        self._ensure_open()
        self.tables.update(up.tables)
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
