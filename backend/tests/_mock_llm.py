"""
_mock_llm.py — deterministic, network-free LLM doubles for the normal test tier.

`MockProvider` routes on each engine's SYSTEM_TAG and returns canned structured
JSON, recording every call so tests can assert on lean-prompt content and on how
many times the (cached) schema labeller ran.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from app.engines.insight import SYSTEM_TAG as INSIGHT_TAG
from app.engines.interpret import SYSTEM_TAG as INTERPRET_TAG
from app.engines.nl2sql import SYSTEM_TAG as NL2SQL_TAG
from app.engines.schema_ai import SYSTEM_TAG as SCHEMA_TAG
from app.llm.base import LLMProvider, RateLimitError

_Q_RE = re.compile(r"Question:\s*(.*)", re.S)


class MockProvider(LLMProvider):
    """A scriptable provider. No network, fully deterministic."""

    name = "mock"
    model = "mock"

    def __init__(
        self,
        *,
        nl2sql: Callable[[str], dict] | dict | None = None,
        mapping: Callable[[str], dict] | dict | None = None,
        insight: dict | None = None,
        label_overrides: dict[tuple[str, str], dict] | None = None,
        default_confidence: float = 0.9,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self._nl2sql = nl2sql
        self._mapping = mapping  # Tier-2 structured-mapping script (interpret.py)
        self._insight = insight or {
            "insight": "Mock insight.",
            "followups": ["Follow-up one?", "Follow-up two?"],
        }
        self._label_overrides = label_overrides or {}
        self._default_confidence = default_confidence

    # ---- introspection helpers for tests ----
    def calls_with(self, tag: str) -> list[tuple[str, str]]:
        return [c for c in self.calls if tag in c[0]]

    # ---- provider impl ----
    def _raw_complete(self, system: str, user: str, *, max_tokens: int) -> str:
        self.calls.append((system, user))
        if SCHEMA_TAG in system:
            return self._labels(user)
        if INTERPRET_TAG in system:
            return self._map(user)
        if NL2SQL_TAG in system:
            return self._sql(user)
        if INSIGHT_TAG in system:
            return json.dumps(self._insight)
        return "{}"

    def _map(self, user: str) -> str:
        m = _Q_RE.search(user)
        question = (m.group(1).strip().splitlines()[0] if m else "").strip()
        resp = self._mapping(question) if callable(self._mapping) else self._mapping
        if resp is None:
            # default: cannot map (fail-loud path) unless a test scripts otherwise
            resp = {"tables": [], "columns": [], "filters": [], "aggregation": None,
                    "measure": None, "group_by": [], "confidence": "low",
                    "alternatives": [], "unmappable": True,
                    "reason": "no scripted mapping for this question"}
        return json.dumps(resp)

    def _labels(self, user: str) -> str:
        digest = json.loads(user.split("Profiles:\n", 1)[1])
        tables: dict = {}
        for table, cols in digest.items():
            out_cols = {}
            for col in cols:
                ov = self._label_overrides.get((table, col), {})
                out_cols[col] = {
                    "meaning": ov.get("meaning", f"the {col} field"),
                    "confidence": ov.get("confidence", self._default_confidence),
                    "clarifying_question": ov.get("clarifying_question"),
                }
            tables[table] = {"summary": f"{table} table", "columns": out_cols}
        return json.dumps({"tables": tables})

    def _sql(self, user: str) -> str:
        m = _Q_RE.search(user)
        question = (m.group(1).strip().splitlines()[0] if m else "").strip()
        resp = self._nl2sql(question) if callable(self._nl2sql) else self._nl2sql
        if resp is None:
            resp = {
                "sql": None,
                "tables_used": [],
                "assumptions": [],
                "needs_clarification": True,
                "clarifying_question": "No scripted answer for this question.",
            }
        return json.dumps(resp)


class FlakyProvider(LLMProvider):
    """Raises RateLimitError `fail_times` times, then succeeds — for backoff tests."""

    name = "flaky"

    def __init__(self, fail_times: int, payload: dict | None = None) -> None:
        self.fail_times = fail_times
        self.attempts = 0
        self._payload = payload or {"ok": True}

    def _raw_complete(self, system: str, user: str, *, max_tokens: int) -> str:
        if self.attempts < self.fail_times:
            self.attempts += 1
            raise RateLimitError("simulated 429")
        return json.dumps(self._payload)
