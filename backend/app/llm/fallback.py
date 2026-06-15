"""
fallback.py — automatic rate-limit fallback wrapper.

In DEFAULT mode a request runs on the primary provider (Gemini). If the primary
is rate-limited (429), the SAME call is transparently retried on the fallback
provider (Groq) so the user never sees the error. The wrapper records that a
fallback happened so the answer can carry a small, honest note.

STRICT PRIVACY RULE (enforced by NOT constructing this wrapper in privacy mode):
this class only ever exists when the user is in default mode. In Privacy Mode the
session uses the bare Groq provider with no fallback, so the user's data is never
silently routed to a provider they opted out of.

Only `RateLimitError` triggers the fallback — every other failure propagates,
keeping behaviour deterministic and testable.
"""
from __future__ import annotations

from app.llm.base import LLMProvider, RateLimitError


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
        except RateLimitError:
            # primary is rate-limited → retry the identical call on the fallback.
            self.used_fallback = True
            return self.fallback._raw_complete(system, user, max_tokens=max_tokens)
