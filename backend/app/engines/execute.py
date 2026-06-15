"""
execute.py — validated SQL → in-memory DuckDB → DataFrame.

Phase 3 keeps this minimal: spin up an in-memory DuckDB, register the cleaned
tables, run the (already guardrail-validated) SELECT, return a DataFrame, and
close the connection. The full web-session lifecycle + wipe verification is
Phase 4; nothing is persisted to disk here either way.
"""
from __future__ import annotations

import duckdb
import pandas as pd


def run_query(
    tables: dict[str, pd.DataFrame],
    sql: str,
    con: "duckdb.DuckDBPyConnection | None" = None,
) -> pd.DataFrame:
    """Load `tables` into an in-memory DuckDB and execute `sql`.

    `sql` MUST already have passed guardrail.validate_sql — this function does no
    validation of its own.

    If `con` is given (a session-owned in-memory connection, the Phase-4 privacy
    mechanism), the query runs against it and the connection is left open for the
    session's lifetime. If `con` is None, a fresh in-memory connection is created
    and closed here — nothing is ever written to disk either way."""
    own = con is None
    if own:
        con = duckdb.connect(database=":memory:")
    try:
        for name, df in tables.items():
            con.register(name, df)
        return con.execute(sql).fetchdf()
    finally:
        if own:
            con.close()  # nothing written to disk; connection (and data) gone
