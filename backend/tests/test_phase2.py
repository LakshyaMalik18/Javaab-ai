"""
test_phase2.py — my own tests for the deterministic Phase-2 engines:
profiler, value-based joins, and the SQL guardrail.

Written test-first against the §9 corpus + cases the audit corpus doesn't cover
(a no-valid-join scenario, a destructive-query set for the guardrail).
"""
import pandas as pd
import pytest

from app.engines import profiler as P
from app.engines import joins as J
from app.engines import guardrail as G


# --------------------------------------------------------------------------
# Profiler
# --------------------------------------------------------------------------
def test_profiler_roles_and_pk():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4],
        "email": ["a@x.com", "b@x.com", "c@x.com", "d@x.com"],
        "segment": ["ENT", "SMB", "ENT", "SMB"],
        "amount": [10.5, 20.0, 5.25, 99.9],
    })
    prof = P.profile_table(df, "customers")
    assert prof["id"]["is_id"] is True
    assert prof["id"]["role"] == "id"
    assert "email" in prof["email"]["pattern_fingerprint"]
    assert prof["segment"]["role"] == "dimension"      # low cardinality text
    assert prof["amount"]["role"] == "measure"
    assert prof["amount"]["numeric_min"] == 5.25


def test_profiler_non_unique_is_not_pk():
    df = pd.DataFrame({"customer_id": [1, 1, 2, 2]})
    prof = P.profile_table(df, "orders")
    assert prof["customer_id"]["is_id"] is False
    assert prof["customer_id"]["_unique"] is False


# --------------------------------------------------------------------------
# Joins
# --------------------------------------------------------------------------
def _profiled(tables):
    return {t: P.profile_table(df, t) for t, df in tables.items()}


def test_join_found_by_value_containment():
    tables = {
        "customers": pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]}),
        "orders": pd.DataFrame({"order_id": [10, 11, 12], "customer_id": [1, 2, 1]}),
    }
    rels = J.discover_joins(tables, _profiled(tables))
    match = [r for r in rels
             if {(r["from_table"], r["from_col"]), (r["to_table"], r["to_col"])}
             == {("orders", "customer_id"), ("customers", "id")}]
    assert match, f"expected orders.customer_id -> customers.id, got {rels}"
    assert match[0]["confidence"] >= 0.8
    assert match[0]["from_table"] == "orders"   # FK is the many side


def test_no_valid_join_when_values_disjoint():
    """Two unrelated tables: no shared values => no relationship invented."""
    tables = {
        "weather": pd.DataFrame({"city_id": [101, 102, 103], "temp": [20, 21, 22]}),
        "invoices": pd.DataFrame({"invoice_id": [1, 2, 3], "total": [9, 8, 7]}),
    }
    rels = J.discover_joins(tables, _profiled(tables))
    assert rels == [], f"should find no join, got {rels}"


def test_join_ignores_low_cardinality_noise():
    """A shared boolean/flag column must not be mistaken for a key."""
    tables = {
        "a": pd.DataFrame({"a_id": [1, 2, 3], "active": [True, False, True]}),
        "b": pd.DataFrame({"b_id": [7, 8, 9], "active": [True, True, False]}),
    }
    rels = J.discover_joins(tables, _profiled(tables))
    assert rels == [], f"flag column should not join, got {rels}"


# --------------------------------------------------------------------------
# Guardrail — the §10 acceptance bar: 100% of destructive queries blocked
# --------------------------------------------------------------------------
SCHEMA = {
    "customers": {"id", "name", "segment"},
    "orders": {"order_id", "customer_id", "amount"},
}

DESTRUCTIVE = [
    "DROP TABLE customers",
    "DELETE FROM orders",
    "DELETE FROM orders WHERE amount < 10",
    "UPDATE customers SET name = 'x'",
    "INSERT INTO customers VALUES (9, 'z', 'ENT')",
    "ALTER TABLE customers ADD COLUMN x INT",
    "TRUNCATE customers",
    "CREATE TABLE evil AS SELECT * FROM customers",
    "ATTACH DATABASE 'evil.db' AS e",
    "COPY customers TO 'out.csv'",
    "PRAGMA database_list",
    "INSTALL httpfs",
    "LOAD httpfs",
    "SELECT * FROM customers; DROP TABLE customers",        # piggy-backed
    "SELECT * FROM customers; DELETE FROM orders",
    "DROP TABLE customers; --",
    "select 1 INTO outfile '/tmp/x'",                       # not standard SELECT
    "REPLACE INTO customers VALUES (1,'a','b')",
    "GRANT ALL ON customers TO public",
    "VACUUM",
]


@pytest.mark.parametrize("sql", DESTRUCTIVE)
def test_destructive_queries_all_blocked(sql):
    res = G.validate_sql(sql, SCHEMA)
    assert res.allowed is False, f"NOT BLOCKED: {sql} -> {res.reason}"


def test_100_percent_destructive_blocked_metric():
    m = G.GuardrailMetrics()
    for sql in DESTRUCTIVE:
        G.validate_sql(sql, SCHEMA, metrics=m)
    assert m.blocked == len(DESTRUCTIVE)
    assert m.pct_blocked == 100.0


VALID = [
    "SELECT * FROM customers",
    "SELECT id, name FROM customers WHERE segment = 'ENT'",
    "SELECT c.name, SUM(o.amount) AS total FROM customers c "
    "JOIN orders o ON o.customer_id = c.id GROUP BY c.name",
    "WITH t AS (SELECT customer_id, amount FROM orders) "
    "SELECT customer_id, SUM(amount) AS s FROM t GROUP BY customer_id",
]


@pytest.mark.parametrize("sql", VALID)
def test_valid_selects_allowed(sql):
    res = G.validate_sql(sql, SCHEMA)
    assert res.allowed is True, f"wrongly blocked: {sql} -> {res.reason}"


def test_unknown_table_blocked():
    res = G.validate_sql("SELECT * FROM secrets", SCHEMA)
    assert res.allowed is False
    assert "unknown table" in res.reason


def test_unknown_column_blocked():
    res = G.validate_sql("SELECT ssn FROM customers", SCHEMA)
    assert res.allowed is False
    assert "unknown column" in res.reason


def test_limit_injected_when_missing():
    res = G.validate_sql("SELECT * FROM customers", SCHEMA, max_rows=500)
    assert res.allowed is True
    assert "limit 500" in res.sql.lower()


def test_oversized_limit_capped():
    res = G.validate_sql("SELECT * FROM customers LIMIT 100000", SCHEMA, max_rows=500)
    assert res.allowed is True
    assert "100000" not in res.sql
    assert "500" in res.sql


def test_small_limit_preserved():
    res = G.validate_sql("SELECT * FROM customers LIMIT 10", SCHEMA, max_rows=500)
    assert res.allowed is True
    assert "10" in res.sql


# --------------------------------------------------------------------------
# Destructive NL-intent detection (pre-SQL guardrail layer)
# --------------------------------------------------------------------------
def test_destructive_intent_detects_write_phrasing():
    for q in [
        "delete all orders",
        "DROP TABLE customers",
        "please truncate the orders table",
        "wipe everything",
        "remove all the customers",
        "get rid of everything",
    ]:
        assert G.destructive_intent(q) is not None, q


def test_destructive_intent_ignores_analytical_phrasing():
    for q in [
        "show me where revenue dropped last quarter",
        "remove duplicates and count distinct customers",
        "which records were updated most recently?",
        "top 5 customers by spend",
        "",
    ]:
        assert G.destructive_intent(q) is None, q
