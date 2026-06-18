"""
test_llm_fallback.py — the fallback's trigger conditions (Fix 1).

FallbackProvider must fall back to Groq on *transient availability* failures of
the primary (Gemini) — 429, 503, other 5xx, and timeout/network errors — but must
NOT fall back when the request or config itself is wrong (4xx auth/validation, or
a missing-key LLMConfigError), because silently retrying a broken request on Groq
would hide a real problem.

These tests drive the full chain: a fake `requests.post` produces the HTTP
condition, the real GeminiProvider maps it to an exception, and the real
FallbackProvider decides whether to reach Groq.
"""
from __future__ import annotations

import pytest
import requests

from app.llm import FallbackProvider, GeminiProvider, GroqProvider
from app.llm.base import (
    LLMConfigError,
    LLMError,
    ProviderUnavailableError,
    RateLimitError,
)


class _FakeResp:
    def __init__(self, status_code: int, body: str = "{}"):
        self.status_code = status_code
        self.text = body

    def json(self):
        # a well-formed Gemini success body, used only on the 200 path
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}


def _fb(monkeypatch, *, status=None, raise_exc=None):
    """Build a FallbackProvider(Gemini→Groq) whose primary HTTP call is faked.

    Returns (fallback, groq) where `groq.called` records whether the fallback was
    actually reached. Keys are injected so neither provider raises LLMConfigError.
    """
    if raise_exc is not None:
        def fake_post(*a, **k):
            raise raise_exc
    else:
        def fake_post(*a, **k):
            return _FakeResp(status)

    monkeypatch.setattr("requests.post", fake_post)

    groq = GroqProvider(api_key="groq-key")
    groq.called = False
    orig = groq._raw_complete

    def tracked(system, user, *, max_tokens):
        groq.called = True
        return '{"answered": "by groq"}'

    groq._raw_complete = tracked  # type: ignore[method-assign]

    primary = GeminiProvider(api_key="gemini-key")
    return FallbackProvider(primary, groq), groq


def _call(fb):
    return fb._raw_complete("sys", "user", max_tokens=64)


# ── transient → falls back ───────────────────────────────────────────────────

def test_503_overload_falls_back_to_groq(monkeypatch):
    fb, groq = _fb(monkeypatch, status=503)
    out = _call(fb)
    assert groq.called is True
    assert fb.used_fallback is True
    assert out == '{"answered": "by groq"}'


def test_other_5xx_falls_back_to_groq(monkeypatch):
    fb, groq = _fb(monkeypatch, status=500)
    _call(fb)
    assert groq.called is True
    assert fb.used_fallback is True


def test_timeout_falls_back_to_groq(monkeypatch):
    fb, groq = _fb(monkeypatch, raise_exc=requests.exceptions.Timeout("read timed out"))
    out = _call(fb)
    assert groq.called is True
    assert fb.used_fallback is True
    assert out == '{"answered": "by groq"}'


def test_connection_error_falls_back_to_groq(monkeypatch):
    fb, groq = _fb(
        monkeypatch, raise_exc=requests.exceptions.ConnectionError("no route")
    )
    _call(fb)
    assert groq.called is True
    assert fb.used_fallback is True


def test_429_still_falls_back(monkeypatch):
    # the original behaviour must be preserved
    fb, groq = _fb(monkeypatch, status=429)
    _call(fb)
    assert groq.called is True
    assert fb.used_fallback is True


# ── request/config is wrong → does NOT fall back, surfaces ───────────────────

def test_400_bad_request_does_not_fall_back(monkeypatch):
    fb, groq = _fb(monkeypatch, status=400)
    with pytest.raises(LLMError) as ei:
        _call(fb)
    # not a transient error → surfaces as a plain LLMError, Groq never touched
    assert not isinstance(ei.value, (RateLimitError, ProviderUnavailableError))
    assert groq.called is False
    assert fb.used_fallback is False


def test_auth_error_403_does_not_fall_back(monkeypatch):
    fb, groq = _fb(monkeypatch, status=403)
    with pytest.raises(LLMError):
        _call(fb)
    assert groq.called is False
    assert fb.used_fallback is False


def test_missing_key_config_error_does_not_fall_back(monkeypatch):
    # no key anywhere → LLMConfigError must surface, not be hidden by a Groq retry
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    groq = GroqProvider(api_key="groq-key")
    groq.called = False

    def tracked(system, user, *, max_tokens):
        groq.called = True
        return "{}"

    groq._raw_complete = tracked  # type: ignore[method-assign]

    fb = FallbackProvider(GeminiProvider(api_key=None), groq)
    with pytest.raises(LLMConfigError):
        _call(fb)
    assert groq.called is False
    assert fb.used_fallback is False
