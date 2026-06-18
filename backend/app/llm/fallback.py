"""
fallback.py — automatic rate-limit fallback wrapper.

In DEFAULT mode a request runs on the primary provider (Gemini). If the primary
is *temporarily unavailable* — rate-limited (429), overloaded (503 / other 5xx),
or it timed out / failed to connect — the SAME call is transparently retried on
the fallback provider (Groq) so the user never sees the error. The wrapper records
that a fallback happened so the answer can carry a small, honest note.

STRICT PRIVACY RULE (enforced by NOT constructing this wrapper in privacy mode):
this class only ever exists when the user is in default mode. In Privacy Mode the
session uses the bare Groq provider with no fallback, so the user's data is never
silently routed to a provider they opted out of.

Only *transient availability* failures trigger the fallback: `RateLimitError`
(429) and `ProviderUnavailableError` (503 / 5xx / timeout / network). Failures
that mean the request or config itself is wrong — a 4xx auth/validation error or
a missing-key `LLMConfigError` (both plain `LLMError`s, not `ProviderUnavailableError`)
— propagate untouched, so a real misconfiguration surfaces loudly instead of being
hidden behind a silent retry on Groq.
"""
from __future__ import annotations

from app.llm.base import LLMProvider, ProviderUnavailableError, RateLimitError


class FallbackProvider(LLMProvider):
    """Wrap a primary provider; on a 429 from it, retry the call on a fallback."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        # present as the primary to the rest of the system
        self.name = primary.name
        self.model = primary.model
        self.used_fallback = False

    def reset_fallback(self) -> None:
        """Clear the per-request flag before a new request runs."""
        self.used_fallback = False

    def _raw_complete(self, system: str, user: str, *, max_tokens: int) -> str:
        try:
            return self.primary._raw_complete(system, user, max_tokens=max_tokens)
        except (RateLimitError, ProviderUnavailableError):
            # primary is temporarily unavailable (429 / 503 / 5xx / timeout) →
            # retry the identical call on the fallback. A bad-request/auth/config
            # error is NOT one of these and propagates instead.
            self.used_fallback = True
            return self.fallback._raw_complete(system, user, max_tokens=max_tokens)
