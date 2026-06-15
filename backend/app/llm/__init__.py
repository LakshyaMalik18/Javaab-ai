"""
LLM provider layer. One interface (`LLMProvider`), two providers:

  - Gemini 2.5 Flash-Lite  → DEFAULT (big free context, survives a hosted demo)
  - Groq llama-3.3-70b      → PRIVACY MODE (no prompt retention)

`get_provider()` is the single factory the rest of the backend calls.
"""
from __future__ import annotations

from app.llm.base import (
    LLMConfigError,
    LLMError,
    LLMProvider,
    LLMResponseError,
    RateLimitError,
    extract_json,
)
from app.llm.fallback import FallbackProvider
from app.llm.gemini import GeminiProvider
from app.llm.groq import GroqProvider


def get_provider(
    privacy_mode: bool = False,
    user_key: str | None = None,
) -> LLMProvider:
    """Return the provider for this session.

    Default  → Gemini 2.5 Flash-Lite.
    privacy_mode=True → Groq (no-retention).
    `user_key` is a per-session, user-supplied key used for this call only and
    never stored server-side.
    """
    if privacy_mode:
        return GroqProvider(api_key=user_key)
    return GeminiProvider(api_key=user_key)


__all__ = [
    "LLMProvider",
    "LLMError",
    "RateLimitError",
    "LLMConfigError",
    "LLMResponseError",
    "extract_json",
    "GeminiProvider",
    "GroqProvider",
    "FallbackProvider",
    "get_provider",
]
