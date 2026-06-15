"""
test_canonical.py — CANONICAL engine (value canonicalization + fuzzy near-dups).
Phase 1 must pass these. This is the GROUP-BY-correctness moat.
"""
import pytest
from _harness import run_pipeline


def test_08_country_variants_collapse_to_one():
    r = run_pipeline("08_canonicalize")
    df = r.table("data")
    countries = set(df["country"].astype(str))
    # USA / U.S.A. / America / United States -> ONE label; Canada untouched
    assert len(countries) == 2
    assert "Canada" in countries
    # group-by correctness: the US cluster sums to 500, Canada to 80
    grouped = df.groupby("country")["sales"].sum().to_dict()
    us_label = [c for c in grouped if c != "Canada"][0]
    assert grouped[us_label] == pytest.approx(500)
    assert grouped["Canada"] == pytest.approx(80)
    # the merge is logged (transparent + reversible)
    assert r.ledger_for("data", "country")


def test_09_fuzzy_near_duplicates_are_flagged():
    r = run_pipeline("09_near_dupes")
    # "Acme Inc" ~ "Acme, Inc." and "Beta LLC" ~ "beta llc" must be caught by fuzzy,
    # which exact-matching would miss. Flagged for keep/remove, never auto-deleted.
    assert r.has_flag("near_duplicate", table="data")
    assert r.table("data").shape[0] == 6   # nothing removed automatically
