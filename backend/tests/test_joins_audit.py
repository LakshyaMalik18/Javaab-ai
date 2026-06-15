"""
test_joins.py — JOINS engine (value-containment relationship discovery — the headline).
Marked `joins`: skipped in Phase 1 (pytest -m "not joins and not profiler").
"""
import pytest
from _harness import run_pipeline

pytestmark = pytest.mark.joins


def test_02_clean_pair_joins_with_high_confidence():
    r = run_pipeline("02_join_pair")
    rel = r.relationship("orders", "customer_id", "customers", "id")
    assert rel is not None
    assert rel["confidence"] >= 0.8


def test_06_coded_headers_join_by_value_containment():
    r = run_pipeline("06_coded_headers")
    # the moat: links by VALUE overlap even though both columns are named cst_id gibberish
    rel = r.relationship("ordr", "cst_id", "cstm", "cst_id")
    assert rel is not None


def test_10_excel_sheets_join_on_category_id():
    r = run_pipeline("10_multisheet_json", files=["workbook.xlsx"])
    rel = r.relationship("products", "category_id", "categories", "category_id")
    assert rel is not None
