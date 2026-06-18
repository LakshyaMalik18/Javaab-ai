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


def test_measure_column_does_not_coincidentally_join_id_range():
    """The number-column false positive (events/bonds shape):

    `events.bondid` is a real FK into `bonds.bondid` and must survive. But
    `bonds.frequency` and `bonds.monthssincecoupon` are *measures* whose small
    integer domains happen to fall inside the contiguous `events.eventid` range
    (1..N) — those coincidental overlaps must be dropped, not surfaced as joins.
    """
    nb, ne = 40, 200
    bonds = pd.DataFrame({
        "bondid": list(range(1, nb + 1)),                          # PK
        "frequency": [[1, 2, 4, 12][i % 4] for i in range(nb)],    # measure
        "monthssincecoupon": [i % 12 + 1 for i in range(nb)],      # measure
    })
    events = pd.DataFrame({
        "eventid": list(range(1, ne + 1)),                 # contiguous id range / PK
        "bondid": [(i % nb) + 1 for i in range(ne)],       # genuine FK -> bonds
    })
    tables = {"bonds": bonds, "events": events}
    rels = J.discover_joins(tables, _profiled(tables))

    def _pair(r):
        return frozenset({(r["from_table"], r["from_col"]),
                          (r["to_table"], r["to_col"])})

    pairs = {_pair(r) for r in rels}
    bondid = frozenset({("events", "bondid"), ("bonds", "bondid")})
    freq = frozenset({("bonds", "frequency"), ("events", "eventid")})
    msc = frozenset({("bonds", "monthssincecoupon"), ("events", "eventid")})

    assert bondid in pairs, f"genuine bondid->bondid join lost: {rels}"
    assert freq not in pairs, f"coincidental frequency->eventid surfaced: {rels}"
    assert msc not in pairs, f"coincidental monthssincecoupon->eventid surfaced: {rels}"


def test_text_dimension_column_does_not_coincidentally_join():
    """The non-numeric coincidence (events/bonds shape):

    `events.bondid` is a real, name-corroborated FK into `bonds.bondid`. But
    `events.desk` is a *dimension* whose handful of values happen to be valid bond
    ids — a coincidental containment with no naming relationship. It must be
    demoted/dropped, leaving bondid->bondid as the only high-confidence option.
    """
    nb, ne = 40, 200
    bond_ids = [f"BOND{i}" for i in range(1, nb + 1)]   # text PK
    bonds = pd.DataFrame({
        "bondid": bond_ids,
        "name": [f"Bond {i}" for i in range(1, nb + 1)],
    })
    events = pd.DataFrame({
        "eventid": list(range(1, ne + 1)),
        "bondid": [bond_ids[i % nb] for i in range(ne)],            # genuine FK
        # a low-cardinality desk label that, by luck, is always a real bond id
        "desk": [bond_ids[i % 3] for i in range(ne)],              # dimension noise
    })
    tables = {"bonds": bonds, "events": events}
    rels = J.discover_joins(tables, _profiled(tables))

    by_pair = {
        frozenset({(r["from_table"], r["from_col"]), (r["to_table"], r["to_col"])}): r
        for r in rels
    }
    bondid = frozenset({("events", "bondid"), ("bonds", "bondid")})
    desk = frozenset({("events", "desk"), ("bonds", "bondid")})

    assert bondid in by_pair, f"genuine bondid->bondid join lost: {rels}"
    assert by_pair[bondid]["confidence_label"] == "high"
    assert desk not in by_pair, f"coincidental desk->bondid surfaced: {rels}"


# --------------------------------------------------------------------------
# Active-link selection (Part B): exactly one load-bearing link per table-pair,
# and only that link is valid at query time.
# --------------------------------------------------------------------------
from app.models import RelationshipEdge, SchemaContract


def _two_candidate_contract() -> SchemaContract:
    """events↔bonds has two discovered candidates on the SAME pair: the genuine
    bondid↔bondid (high) and a weaker bondid↔eventid alternative."""
    return SchemaContract(relationships=[
        RelationshipEdge(from_table="events", from_col="bondid",
                         to_table="bonds", to_col="bondid",
                         confidence=1.0, confidence_label="high"),
        RelationshipEdge(from_table="bonds", from_col="bondid",
                         to_table="events", to_col="eventid",
                         confidence=0.97, confidence_label="high"),
    ])


def test_default_activation_one_per_pair_highest_confidence():
    c = _two_candidate_contract()
    c.default_activate_relationships()
    active = c.active_relationships()
    assert len(active) == 1, f"exactly one active link per pair, got {active}"
    a = active[0]
    assert (a.from_col, a.to_col) == ("bondid", "bondid")   # the highest-confidence one


def test_set_active_link_switches_and_stays_single():
    c = _two_candidate_contract()
    c.default_activate_relationships()
    ok = c.set_active_link("bonds", "bondid", "events", "eventid")
    assert ok is True
    active = c.active_relationships()
    assert len(active) == 1, "still exactly one active after switching"
    assert active[0].to_col == "eventid"


def test_set_active_link_unknown_returns_false():
    c = _two_candidate_contract()
    c.default_activate_relationships()
    assert c.set_active_link("bonds", "nope", "events", "eventid") is False


def test_only_active_link_is_a_valid_join_key():
    """The whole point: a join on the ACTIVE link passes the guard; a join on the
    inactive (alternative) link is rejected as an invalid join key."""
    c = _two_candidate_contract()
    c.default_activate_relationships()  # active = events.bondid = bonds.bondid
    schema = {"events": {"bondid", "eventid"}, "bonds": {"bondid", "name"}}

    active_join = ("SELECT b.name FROM events e "
                   "JOIN bonds b ON e.bondid = b.bondid")
    inactive_join = ("SELECT b.name FROM bonds b "
                     "JOIN events e ON b.bondid = e.eventid")

    ok = G.validate_sql(active_join, schema, relationships=c.active_relationships())
    assert ok.allowed is True, ok.reason

    bad = G.validate_sql(inactive_join, schema, relationships=c.active_relationships())
    assert bad.allowed is False
    assert bad.kind == "invalid_join_key", bad.kind

    # after the user switches the active link, the verdicts flip
    c.set_active_link("bonds", "bondid", "events", "eventid")
    flipped = G.validate_sql(inactive_join, schema, relationships=c.active_relationships())
    assert flipped.allowed is True, flipped.reason


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
