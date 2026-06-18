"""
groq.py — PRIVACY-MODE provider. Groq `llama-3.3-70b-versatile`.

Selected by the `privacy_mode` flag because Groq's policy is not to retain
inference data (CLAUDE.md §1/§11). OpenAI-compatible chat-completions REST API,
called with `requests` (no SDK dependency). Key source: GROQ_API_KEY env var, or
a per-call user-supplied key — never hardcoded, never stored.
"""
from __future__ import annotations

import os

from app.llm.base import (
    LLMConfigError,
    LLMError,
    LLMProvider,
    ProviderUnavailableError,
    RateLimitError,
)

_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "llama-3.3-70b-versatile",
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.model = model
        self._timeout = timeout

    def _require_key(self) -> str:
        if not self._api_key:
            raise LLMConfigError(
                "No Groq API key. Set GROQ_API_KEY or pass a user-supplied key."
            )
        return self._api_key

    def _raw_complete(self, system: str, user: str, *, max_tokens: int) -> str:
        import requests

        key = self._require_key()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            resp = requests.post(
                _ENDPOINT,
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            # timeout / connection error → temporary; safe to fall back.
            raise ProviderUnavailableError(f"Groq request failed: {e}") from e

        if resp.status_code == 429:
            raise RateLimitError("Groq rate limit exceeded (429).")
        if resp.status_code >= 500:
            # 503 overload / other 5xx → temporary; safe to fall back.
            raise ProviderUnavailableError(
                f"Groq temporarily unavailable (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            # 4xx (bad request, 401/403 bad key) → surface loudly, do not silently retry.
            raise LLMError(f"Groq HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected Groq response shape: {data}") from e
