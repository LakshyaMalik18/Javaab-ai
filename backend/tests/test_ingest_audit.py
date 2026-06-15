"""
test_ingest.py — INGEST engine (structure detection, multi-format, graceful failure).
Phase 1 must pass these. Run cleaning-only: pytest -m "not joins and not profiler"
"""
import pytest
from _harness import run_pipeline


def test_01_clean_basic_loads_one_table():
    r = run_pipeline("01_clean_basic")
    assert not r.raised
    df = r.table("data")
    assert df.shape == (3, 4)
    assert list(df.columns) == ["order_id", "product", "quantity", "price"]


def test_07_skips_preamble_and_flattens_two_row_header():
    r = run_pipeline("07_preamble_header")
    assert not r.raised
    df = r.table("data")
    # 3 data rows survive (North/South/West); preamble + blank line dropped
    assert df.shape[0] == 3
    # first column is Region; the doubled 2026 header is flattened into the measures
    cols = [str(c).lower() for c in df.columns]
    assert any("region" in c for c in cols)
    assert sum(("revenue" in c) for c in cols) == 1
    assert sum(("units" in c) for c in cols) == 1


def test_10_xlsx_becomes_two_tables():
    r = run_pipeline("10_multisheet_json", files=["workbook.xlsx"])
    assert "products" in r.tables and "categories" in r.tables
    assert r.table("products").shape[0] == 2
    assert r.table("categories").shape[0] == 2


def test_10_json_flattens_nested_and_handles_ragged_keys():
    r = run_pipeline("10_multisheet_json", files=["nested.json"])
    # one table from the array; nested address.* flattened
    df = next(iter(r.tables.values()))
    cols = [str(c) for c in df.columns]
    assert any(c.endswith("city") for c in cols)
    assert any(c.endswith("zip") for c in cols)
    # Bob has no zip -> must be NULL, not a crash
    zip_col = [c for c in cols if c.endswith("zip")][0]
    assert df[zip_col].isna().sum() == 1


def test_11_empty_file_is_graceful():
    r = run_pipeline("11_degenerate", files=["empty.csv"])
    assert not r.raised                       # never throws
    assert r.errors                           # reports a clear problem
    assert not r.tables                        # no table built from an empty file


def test_11_header_only_yields_zero_rows():
    r = run_pipeline("11_degenerate", files=["header_only.csv"])
    assert not r.raised
    df = next(iter(r.tables.values()))
    assert df.shape[0] == 0
    assert list(df.columns) == ["col_a", "col_b"]


def test_11_single_column_is_valid():
    r = run_pipeline("11_degenerate", files=["single_col.csv"])
    assert not r.raised
    df = next(iter(r.tables.values()))
    assert df.shape[1] == 1


def test_12_weird_encoding_decodes_correctly():
    r = run_pipeline("12_garbage_encoding", files=["weird_encoding.csv"])
    assert not r.raised
    df = next(iter(r.tables.values()))
    names = set(df["name"].astype(str))
    # BOM stripped, latin-1 decoded -> accents preserved, no mojibake
    assert "José" in names
    assert "Zoë" in names


def test_12_non_tabular_garbage_is_rejected_gracefully():
    r = run_pipeline("12_garbage_encoding", files=["not_tabular.csv"])
    assert not r.raised
    assert r.errors            # rejected with a message, not a stack trace
