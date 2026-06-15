"""
base.py — the one LLMProvider interface every provider implements.

Design rules (CLAUDE.md §1):
  - Structured JSON output: `complete_json` always returns a parsed dict.
  - Exponential-backoff retry with a *distinct* RateLimitError type so the UI can
    show a friendly "slow down" message instead of a generic failure.
  - Keys are NEVER hardcoded. Providers read their key from the environment, or
    accept a per-call user-supplied key (which is never stored anywhere).
"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


# ── Error taxonomy ────────────────────────────────────────────────────────────

class LLMError(RuntimeError):
    """Base class for all provider failures."""


class RateLimitError(LLMError):
    """Provider returned 429 / quota exhausted. Distinct so the UI can back off."""


class LLMConfigError(LLMError):
    """Missing API key or misconfiguration — surfaced clearly, never a crash."""


class LLMResponseError(LLMError):
    """Provider replied, but the body wasn't usable JSON."""


@dataclass
class LLMResponse:
    text: str
    raw: dict | None = None


# ── JSON extraction helper ────────────────────────────────────────────────────

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.I)


def extract_json(text: str) -> dict:
    """Pull a JSON object out of an LLM reply, tolerating ```json fences and prose.
    Raises LLMResponseError if nothing parseable is found."""
    if text is None:
        raise LLMResponseError("empty model response")
    candidate = text.strip()

    # ```json ... ``` fenced block wins if present
    m = _FENCE.search(candidate)
    if m:
        candidate = m.group(1).strip()

    # direct parse
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # last resort: grab the outermost {...}
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(candidate[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    raise LLMResponseError(f"could not parse JSON from model response: {text[:200]!r}")


# ── Provider interface ────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """One interface; Groq / Gemini / future Anthropic swap behind it."""

    name: str = "base"
    model: str = ""

    #: exponential-backoff schedule (seconds) used by the retry wrapper
    _BACKOFF = (0.5, 1.0, 2.0, 4.0)

    @abstractmethod
    def _raw_complete(self, system: str, user: str, *, max_tokens: int) -> str:
        """Provider-specific single call. Returns the model's text. May raise
        RateLimitError / LLMError. Retry/JSON parsing live in the base class."""

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        retries: int = 3,
    ) -> dict:
        """Call the model and return a parsed JSON dict, with exponential backoff
        on transient/rate-limit failures."""
        text = self.complete_text(system, user, max_tokens=max_tokens, retries=retries)
        return extract_json(text)

    def complete_text(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1024,
        retries: int = 3,
    ) -> str:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self._raw_complete(system, user, max_tokens=max_tokens)
            except RateLimitError as e:
                last = e
                if attempt >= retries:
                    raise
                self._sleep(attempt)
            except LLMError:
                raise
        # unreachable, but keeps type-checkers happy
        raise last or LLMError("exhausted retries")

    def _sleep(self, attempt: int) -> None:
        idx = min(attempt, len(self._BACKOFF) - 1)
        time.sleep(self._BACKOFF[idx])
