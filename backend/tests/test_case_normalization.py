"""
test_case_normalization.py — regression for the mixed-case categorical bug.

Ground-truth bug: a `status` column with "paid"/"Paid"/"PAID" was filtered
case-sensitively (WHERE status = 'paid'), silently dropping the "Paid"/"PAID"
rows and under-reporting revenue. Two independent layers must each prevent it:

  1. Cleaning/canonicalization collapses casing variants into one value
     (same idea as the USA/U.S.A./America country merge).
  2. The guardrail rewrites text-literal equality to LOWER(col) = 'literal' so a
     casing mismatch can never silently drop rows even if step 1 is bypassed.

Ground-truth paid revenue by country: US 7650.50, UK 4700, Canada 4200.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.engines import canonical as canonical_mod
from app.engines import execute as execute_mod
from app.engines import guardrail as guardrail_mod
from app.models import ChangeLedger

# Mixed-case status + a couple of excluded refunds. Countries are already
# canonical so this test isolates the status-casing fix.
RAW_ORDERS = pd.DataFrame(
    {
        "country": [
            "United States", "United States", "United States", "United States",
            "United Kingdom", "United Kingdom",
            "Canada",
        ],
        "amount": [1240.00, 2100.00, 4310.50, 500.00, 2000.00, 2700.00, 4200.00],
        "status": ["paid", "Paid", "PAID", "refunded", "Paid", "PAID", "paid"],
    }
)

EXPECTED_PAID_BY_COUNTRY = {
    "United States": 7650.50,
    "United Kingdom": 4700.00,
    "Canada": 4200.00,
}

# A deliberately case-sensitive query — what a model might emit.
QUERY = (
    "SELECT country, SUM(amount) AS revenue "
    "FROM orders WHERE status = 'paid' GROUP BY country"
)

SCHEMA = {"orders": {"country", "amount", "status"}}


def _totals(df: pd.DataFrame) -> dict[str, float]:
    return {row["country"]: round(float(row["revenue"]), 2) for _, row in df.iterrows()}


# ── Layer 1: cleaning collapses the casing variants ───────────────────────────
def test_canonicalize_collapses_status_casing():
    ledger = ChangeLedger()
    cleaned, _ = canonical_mod.canonicalize_column(
        RAW_ORDERS["status"], "orders", "status", ledger
    )
    # all of paid/Paid/PAID become one value
    paid_like = {v for v in cleaned.unique() if str(v).lower() == "paid"}
    assert len(paid_like) == 1, f"expected one canonical 'paid', got {paid_like}"
    # the merge is recorded for the change-ledger / undo
    assert any(r.rule == "canonicalize_case" for r in ledger.records)


# ── End-to-end: clean then run the (case-sensitive) query → correct totals ────
def test_paid_revenue_by_country_after_cleaning():
    ledger = ChangeLedger()
    cleaned = RAW_ORDERS.copy()
    cleaned["status"], _ = canonical_mod.canonicalize_column(
        cleaned["status"], "orders", "status", ledger
    )

    gr = guardrail_mod.validate_sql(QUERY, SCHEMA)
    assert gr.allowed, gr.reason
    df = execute_mod.run_query({"orders": cleaned}, gr.sql)

    assert _totals(df) == EXPECTED_PAID_BY_COUNTRY


# ── Layer 2: guardrail alone fixes it even on un-canonicalized raw data ────────
def test_guardrail_case_insensitive_without_cleaning():
    gr = guardrail_mod.validate_sql(QUERY, SCHEMA)
    assert gr.allowed, gr.reason
    # the rewrite is visible in the emitted SQL
    assert "LOWER" in gr.sql.upper()

    df = execute_mod.run_query({"orders": RAW_ORDERS}, gr.sql)
    assert _totals(df) == EXPECTED_PAID_BY_COUNTRY


# ── The guardrail rewrite must not touch dates/numbers or break IN ────────────
def test_guardrail_leaves_dates_and_numbers_alone():
    schema = {"orders": {"order_date", "amount", "status"}}
    sql = (
        "SELECT * FROM orders "
        "WHERE order_date = '2024-01-01' AND amount = 100 "
        "AND status IN ('paid','Refunded')"
    )
    gr = guardrail_mod.validate_sql(sql, schema)
    assert gr.allowed, gr.reason
    out = gr.sql.upper()
    # date literal (no letters) and numeric literal stay un-lowered
    assert "LOWER(ORDER_DATE)" not in out
    assert "'2024-01-01'" in gr.sql
    assert "AMOUNT = 100" in out
    # the text IN-list is folded to lowercase on LOWER(status)
    assert "LOWER(STATUS) IN ('PAID', 'REFUNDED')".replace(" ", "") in out.replace(" ", "")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
