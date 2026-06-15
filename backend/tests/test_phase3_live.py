"""
test_phase3_live.py — LIVE tier. Hits a REAL model. NOT part of the normal run.

    pytest -m live

Confirms the actual provider returns valid, guardrail-passing SQL on the demo
data. Default = Gemini (needs GEMINI_API_KEY). If GROQ_API_KEY is set, also runs
one Groq privacy-mode call.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from _harness import run_pipeline

from app.engines.orchestrator import SessionBrain

# Auto-load backend/.env so a pasted key is picked up without a manual export.
# Done at module import (before the skipif decorators below are evaluated) and
# ONLY in this live module — the mocked suite never imports dotenv side effects
# and still runs with no key. `override=False` keeps a real exported env var
# winning over the file.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:  # python-dotenv not installed → just rely on real env vars
    pass

pytestmark = pytest.mark.live

_QUESTIONS = [
    "How many orders are there?",
    "What is the total amount of all orders?",
    "What is the total order amount for each customer segment?",
]


def _tables_and_flags():
    r = run_pipeline("02_join_pair")
    assert not r.raised, r.errors
    return r.tables, r.flags


def _assert_good(res):
    assert res.status in ("answered", "clarify"), f"{res.status}: {res.error}"
    if res.status == "answered":
        assert res.sql and "SELECT" in res.sql.upper()
        assert res.insight
        # it actually executed against DuckDB
        assert res.columns


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"),
                    reason="GEMINI_API_KEY not set")
def test_live_gemini_default():
    tables, flags = _tables_and_flags()
    brain = SessionBrain(tables, flags=flags)  # default = Gemini
    for q in _QUESTIONS:
        res = brain.ask(q)
        _assert_good(res)
    # guardrail saw only allowed, read-only SQL
    assert brain.metrics.blocked == 0


@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"),
                    reason="GROQ_API_KEY not set")
def test_live_groq_privacy_mode():
    tables, flags = _tables_and_flags()
    brain = SessionBrain(tables, privacy_mode=True, flags=flags)  # Groq
    res = brain.ask(_QUESTIONS[0])
    _assert_good(res)
