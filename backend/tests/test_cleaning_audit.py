"""
test_cleaning.py — CLEANING engine (type coercion, money, dates, nulls, exact dups).
Phase 1 must pass these.
"""
import pytest
import pandas as pd
from _harness import run_pipeline


def _num(series):
    return pd.to_numeric(series, errors="coerce").tolist()


def test_01_clean_file_makes_no_changes():
    r = run_pipeline("01_clean_basic")
    assert r.ledger == []        # nothing to fix on a clean file


def test_03_money_formats_parsed_to_numbers():
    r = run_pipeline("03_money_formats")
    df = r.table("data")
    assert _num(df["revenue"]) == pytest.approx([1234.50, 987.00, -45.00])  # $, commas, ()->negative
    assert _num(df["discount"]) == pytest.approx([0.10, 0.05, 0.00])         # % -> fraction
    assert _num(df["units"]) == pytest.approx([1200, 950, 3400])             # thousands sep
    # transparency: each touched column has at least one ledger entry with before/after
    for col in ("revenue", "discount", "units"):
        entries = r.ledger_for("data", col)
        assert entries, f"no ledger entry for {col}"
        assert all("before_sample" in e and "after_sample" in e for e in entries)


def test_04_dates_resolved_by_whole_column_voting():
    r = run_pipeline("04_ambiguous_dates")
    df = r.table("data")
    # '15' in day position fixes the whole column to DD/MM -> ISO
    assert df["date"].astype(str).tolist() == ["2026-04-03", "2026-04-15", "2026-04-07"]


def test_04_genuinely_ambiguous_column_is_flagged_provisional():
    r = run_pipeline("04_ambiguous_dates")
    # date2 has no value >12 anywhere -> cannot be resolved -> flagged, not silently guessed
    assert r.has_flag("ambiguous_date", table="data", column="date2")
    flag = next(f for f in r.flags if f.get("kind") == "ambiguous_date" and f.get("column") == "date2")
    assert flag.get("provisional") is True


def test_05_null_tokens_unified():
    r = run_pipeline("05_messy_nulls")
    df = r.table("data")
    # NA, -, "" -> NULL, leaving only active/inactive
    assert set(df["status"].dropna().astype(str)) == {"active", "inactive"}
    assert df["status"].isna().sum() == 3
    # "", n/a, N/A, none -> NULL
    assert df["notes"].isna().sum() == 4
    assert r.ledger_for("data", "status")     # logged


def test_06_coded_headers_still_get_cleaned():
    r = run_pipeline("06_coded_headers", files=["ordr.csv"])
    df = r.table("ordr")
    assert _num(df["amt"]) == pytest.approx([250, 500, 125])
    assert df["ord_dt"].astype(str).tolist() == ["2026-04-03", "2026-04-15", "2026-04-07"]


def test_09_exact_duplicate_is_reported_not_deleted():
    r = run_pipeline("09_near_dupes")
    df = r.table("data")
    # report-only: the exact Gamma dup must still be present (never auto-removed)
    assert df.shape[0] == 6
    assert r.has_flag("exact_duplicate", table="data")
