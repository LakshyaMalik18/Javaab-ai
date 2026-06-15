"""
test_profiler.py — PROFILER engine (deterministic column roles / key detection).
Marked `profiler`: skipped in Phase 1 (pytest -m "not joins and not profiler").
"""
import pytest
from _harness import run_pipeline

pytestmark = pytest.mark.profiler


def _prof(r, table, col):
    return r.profiles.get(table, {}).get(col, {})


def test_02_primary_and_foreign_keys_detected():
    r = run_pipeline("02_join_pair")
    assert _prof(r, "customers", "id").get("is_id") is True          # unique, non-null
    assert _prof(r, "orders", "customer_id").get("is_fk") is True    # repeats, references


def test_06_coded_id_columns_profiled_by_values():
    r = run_pipeline("06_coded_headers")
    # despite gibberish names, cst_id is id-like in cstm and fk-like in ordr
    assert _prof(r, "cstm", "cst_id").get("is_id") is True
    assert _prof(r, "ordr", "cst_id").get("is_fk") is True
