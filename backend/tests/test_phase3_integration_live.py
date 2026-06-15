"""
test_phase3_integration_live.py — FULL-PIPELINE smoke test on REAL data with a
REAL Gemini call. No mocks, no shortcuts. Run with:

    pytest -m live -k integration -s

Proves Phases 1→2→3 connect on fixture 06_coded_headers (gibberish headers):
ingest both files → clean → profile → discover joins → build schema contract →
answer() twice. Confirms the model writes a cross-file JOIN on the coded
`cst_id` columns, the SQL passes the guardrail, runs on DuckDB, and returns a
correct result with an insight.

`-s` prints the actual SQL / result / insight for both questions.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from _harness import run_pipeline

from app.engines.orchestrator import SessionBrain

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

pytestmark = pytest.mark.live


def _column_sums(rows: list[dict]) -> list[float]:
    """All numeric column totals — robust to INNER/LEFT join + aliasing."""
    sums: list[float] = []
    if not rows:
        return sums
    for key in rows[0]:
        try:
            sums.append(round(sum(float(r[key]) for r in rows), 2))
        except (TypeError, ValueError):
            continue
    return sums


def _dump(label: str, res) -> None:
    print(f"\n{'='*72}\n{label}\n{'='*72}")
    print(f"status     : {res.status}")
    print(f"SQL        : {res.sql}")
    if res.assumptions:
        print(f"assumptions: {res.assumptions}")
    print(f"result     : {res.rows}")
    print(f"insight    : {res.insight}")
    if res.followups:
        print(f"followups  : {res.followups}")
    if res.clarifying_question:
        print(f"clarify    : {res.clarifying_question}")
    if res.error:
        print(f"error      : {res.error}")


@pytest.mark.skipif(
    not (os.environ.get("GEMINI_API_KEY") or "").strip(),
    reason="GEMINI_API_KEY not set (paste a real key into backend/.env)",
)
def test_integration_full_pipeline_coded_headers_live():
    # Phases 1+2: ingest BOTH files → clean → profile → discover joins.
    r = run_pipeline("06_coded_headers")
    assert not r.raised, r.errors
    assert set(r.tables) == {"cstm", "ordr"}

    # the value-based join must have linked the gibberish cst_id columns
    join = r.relationship("ordr", "cst_id", "cstm", "cst_id")
    assert join is not None, "Phase-2 join on coded cst_id columns not discovered"

    # Phase 3: build the schema contract once (real Gemini labelling) + answer().
    brain = SessionBrain(r.tables, flags=r.flags)  # default = Gemini

    # ── Q1: orders per customer (cross-file JOIN, COUNT) ──────────────────────
    q1 = brain.ask("how many orders per customer")
    _dump("Q1: how many orders per customer", q1)

    assert q1.status == "answered", q1.clarifying_question or q1.error
    sql1 = q1.sql.upper()
    assert "JOIN" in sql1, "expected a cross-file JOIN"
    assert "CST_ID" in sql1, "JOIN must be on the coded cst_id key"
    assert "LIMIT" in sql1, "guardrail should have enforced a LIMIT"
    assert q1.rows, "no rows returned"
    assert q1.insight
    # correctness: 3 orders total across the customers (Alice 2, Bob 1, Carol 0)
    assert 3 in _column_sums(q1.rows), f"order count should total 3, got {q1.rows}"

    # ── Q2: total order amount by customer name (JOIN, SUM, group by name) ─────
    q2 = brain.ask("total order amount by customer name")
    _dump("Q2: total order amount by customer name", q2)

    assert q2.status == "answered", q2.clarifying_question or q2.error
    sql2 = q2.sql.upper()
    assert "JOIN" in sql2
    assert "LIMIT" in sql2
    assert q2.rows and q2.insight
    # correctness: total amount = 250+125+500 = 875
    assert 875.0 in _column_sums(q2.rows), f"amount should total 875, got {q2.rows}"

    # guardrail saw only allowed, read-only SQL across the whole session
    assert brain.metrics.blocked == 0
    assert brain.metrics.allowed >= 2
