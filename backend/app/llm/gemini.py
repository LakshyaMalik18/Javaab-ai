"""
gemini.py — DEFAULT provider. Gemini 2.5 Flash-Lite via the REST API.

Chosen as default (CLAUDE.md / Phase 3 brief) because its free tier has no tight
daily token cap and a 1M-token context, so it survives a hosted multi-user demo.

Key source: the GEMINI_API_KEY env var, or a per-call user-supplied key that is
used for this call only and never stored. Implemented with `requests` (no SDK
dependency) so the provider layer stays thin and easy to mock in tests.
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

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash-lite",
        timeout: float = 60.0,
    ) -> None:
        # explicit user key > env var. Never read from anywhere else; never stored.
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model
        self._timeout = timeout

    def _require_key(self) -> str:
        if not self._api_key:
            raise LLMConfigError(
                "No Gemini API key. Set GEMINI_API_KEY or pass a user-supplied key."
            )
        return self._api_key

    def _raw_complete(self, system: str, user: str, *, max_tokens: int) -> str:
        import requests  # local import: keeps module import-safe without network deps

        key = self._require_key()
        url = _ENDPOINT.format(model=self.model)
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        try:
            resp = requests.post(
                url,
                params={"key": key},
                json=payload,
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            # timeout / connection error → temporary; safe to fall back.
            raise ProviderUnavailableError(f"Gemini request failed: {e}") from e

        if resp.status_code == 429:
            raise RateLimitError("Gemini rate limit / quota exceeded (429).")
        if resp.status_code >= 500:
            # 503 overload / other 5xx → temporary; safe to fall back.
            raise ProviderUnavailableError(
                f"Gemini temporarily unavailable (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            # 4xx (bad request, 401/403 bad key) → the request or auth is wrong;
            # surface loudly, do NOT silently retry elsewhere.
            raise LLMError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected Gemini response shape: {data}") from e
