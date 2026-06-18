"""
test_phase3_multihop.py — multi-table JOIN composition (the headline feature).

Two goals, both required, proven with KNOWN-ANSWER fixtures:

  GOAL 1 (deterministic join-path composition): given the tables a question
  touches, walk the relationship graph and pull in ALL connecting/bridge tables
  (not one accidental hop), with the columns the query actually needs.

  GOAL 2 (a bad join fails LOUD): an incomplete join (fewer tables than the
  resolved path) or a cartesian/cross join must be REFUSED, never executed into a
  confident wrong number.

Test-first: these are written against the desired behaviour and FAIL on the
current code. See the module docstring assertions for the baseline failure mode of
each (dropped table / partial join / cross join / wrong number).
"""
from __future__ import annotations

import pandas as pd
import pytest

from _mock_llm import MockProvider

from app.engines import nl2sql as nl2sql_mod
from app.engines.orchestrator import answer
from app.models import (
    ColumnContract,
    RelationshipEdge,
    SchemaContract,
    TableContract,
)


# ── fixture builders ────────────────────────────────────────────────────────────

def _col(name, role="dimension", dtype="text", **kw):
    return ColumnContract(name=name, raw_name=name, dtype=dtype, role=role, **kw)


# ── SCENARIO A — STAR SCHEMA (orders bridges customers + products) ───────────────
# Question needs all three: "total amount by product category for enterprise segment".
#
# Ground truth (enterprise = customers 1 & 2; smb customer 3 is excluded):
#   widgets (product 10): order1(cust1,100) + order3(cust2,300)              = 400
#   gadgets (product 20): order2(cust1,200) + order5(cust2,50)               = 250
# An all-customers (dropped-customers) partial join would WRONGLY yield:
#   widgets = 100+300+1000 = 1400 ; gadgets = 250   ← the silent wrong number.

def _star():
    contract = SchemaContract(
        tables=[
            TableContract(name="customers", row_count=3, columns=[
                _col("id", "id", "numeric", is_id=True),
                _col("segment"),
            ]),
            TableContract(name="orders", row_count=5, columns=[
                _col("order_id", "id", "numeric", is_id=True),
                _col("customer_id", "id", "numeric", is_id=True, is_fk=True),
                _col("product_id", "id", "numeric", is_id=True, is_fk=True),
                _col("amount", "measure", "numeric", meaning="order amount in USD"),
            ]),
            TableContract(name="products", row_count=2, columns=[
                _col("id", "id", "numeric", is_id=True),
                _col("category"),
            ]),
        ],
        relationships=[
            RelationshipEdge(from_table="orders", from_col="customer_id",
                             to_table="customers", to_col="id",
                             confidence=0.95, confidence_label="high"),
            RelationshipEdge(from_table="orders", from_col="product_id",
                             to_table="products", to_col="id",
                             confidence=0.95, confidence_label="high"),
        ],
    )
    tables = {
        "customers": pd.DataFrame({"id": [1, 2, 3],
                                   "segment": ["enterprise", "enterprise", "smb"]}),
        "products": pd.DataFrame({"id": [10, 20],
                                  "category": ["widgets", "gadgets"]}),
        "orders": pd.DataFrame({
            "order_id":   [1, 2, 3, 4, 5],
            "customer_id": [1, 1, 2, 3, 2],
            "product_id": [10, 20, 10, 10, 20],
            "amount":     [100, 200, 300, 1000, 50],
        }),
    }
    return contract, tables


_STAR_Q = "total amount by product category for the enterprise segment"

_STAR_FULL_SQL = (
    "SELECT p.category, SUM(o.amount) AS total_amount "
    "FROM orders o "
    "JOIN customers c ON o.customer_id = c.id "
    "JOIN products p ON o.product_id = p.id "
    "WHERE c.segment = 'enterprise' "
    "GROUP BY p.category"
)

# drops the customers join + the segment filter → valid SQL, WRONG number
_STAR_PARTIAL_SQL = (
    "SELECT p.category, SUM(o.amount) AS total_amount "
    "FROM orders o "
    "JOIN products p ON o.product_id = p.id "
    "GROUP BY p.category"
)

# comma cross join (no ON between orders and products) → cartesian product
_STAR_CROSS_SQL = (
    "SELECT p.category, SUM(o.amount) AS total_amount "
    "FROM orders o, products p "
    "GROUP BY p.category"
)


def test_star_full_join_known_answer():
    """Happy path: the full 3-table join returns the exact ground truth. This is the
    regression lock — it must stay green before and after the fix."""
    contract, tables = _star()
    mock = MockProvider(nl2sql=lambda q: {
        "sql": _STAR_FULL_SQL, "tables_used": ["orders", "customers", "products"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    res = answer(_STAR_Q, tables, contract=contract, provider=mock)
    assert res.status == "answered", (res.status, res.clarifying_question)
    got = {r["category"]: r["total_amount"] for r in res.rows}
    assert got == {"widgets": 400, "gadgets": 250}


def test_star_resolves_all_three_tables():
    """GOAL 1: the question touches customers + orders + products; the resolved
    join-path must contain all three."""
    contract, _ = _star()
    rel = nl2sql_mod.select_relevant(_STAR_Q, contract)
    for t in ("orders", "customers", "products"):
        assert t in rel.tables, f"{t} missing from resolved tables {sorted(rel.tables)}"


def test_star_partial_join_fails_loud():
    """GOAL 2(a): the model drops the customers table (and the segment filter). That
    is VALID SQL that silently answers the WRONG question (all customers, 1400 not
    400). It must be REFUSED, never executed into a confident wrong number.

    Baseline (current code): returns status='answered' with widgets=1400 → FAILS."""
    contract, tables = _star()
    mock = MockProvider(nl2sql=lambda q: {
        "sql": _STAR_PARTIAL_SQL, "tables_used": ["orders", "products"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    res = answer(_STAR_Q, tables, contract=contract, provider=mock)
    assert res.status in ("refused", "blocked"), (
        f"partial join must fail loud, got {res.status} with rows={res.rows}")
    assert not res.rows


def test_star_cross_join_fails_loud():
    """GOAL 2(b): a comma cross join (no join condition) is a cartesian product —
    never what the user meant. Must be refused.

    Baseline (current code): executes the cartesian → status='answered' → FAILS."""
    contract, tables = _star()
    mock = MockProvider(nl2sql=lambda q: {
        "sql": _STAR_CROSS_SQL, "tables_used": ["orders", "products"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    res = answer(_STAR_Q, tables, contract=contract, provider=mock)
    assert res.status in ("refused", "blocked"), (
        f"cross join must fail loud, got {res.status} with rows={res.rows}")
    assert not res.rows


# ── SCENARIO B — LINEAR 4-HOP CHAIN with CODED KEYS ──────────────────────────────
# country — region — district — store — sale  (5 tables, coded FK/PK names so there
# is NO parent-name leakage — the value-based / coded-header headline scenario).
#
# An A↔E question ("total sales amount by country") must traverse ALL FOUR joins.
# Ground truth:
#   Atlantis: store1000 → sales 500 + 300 = 800
#   Wakanda : store2000 → sale 700              = 700

def _chain():
    contract = SchemaContract(
        tables=[
            TableContract(name="country", columns=[
                _col("c_id", "id", "numeric", is_id=True), _col("country_name")]),
            TableContract(name="region", columns=[
                _col("r_id", "id", "numeric", is_id=True),
                _col("c_ref", "id", "numeric", is_fk=True), _col("region_name")]),
            TableContract(name="district", columns=[
                _col("d_id", "id", "numeric", is_id=True),
                _col("r_ref", "id", "numeric", is_fk=True), _col("district_name")]),
            TableContract(name="store", columns=[
                _col("s_id", "id", "numeric", is_id=True),
                _col("d_ref", "id", "numeric", is_fk=True)]),
            TableContract(name="sale", columns=[
                _col("sale_id", "id", "numeric", is_id=True),
                _col("st_ref", "id", "numeric", is_fk=True),
                _col("amount", "measure", "numeric")]),
        ],
        # ADVERSARIAL order (as discover_joins' confidence-desc sort can produce):
        # the middle edges sort first, defeating the single-pass one-hop expansion.
        relationships=[
            RelationshipEdge(from_table="district", from_col="r_ref",
                             to_table="region", to_col="r_id",
                             confidence=0.99, confidence_label="high"),
            RelationshipEdge(from_table="store", from_col="d_ref",
                             to_table="district", to_col="d_id",
                             confidence=0.98, confidence_label="high"),
            RelationshipEdge(from_table="region", from_col="c_ref",
                             to_table="country", to_col="c_id",
                             confidence=0.85, confidence_label="high"),
            RelationshipEdge(from_table="sale", from_col="st_ref",
                             to_table="store", to_col="s_id",
                             confidence=0.84, confidence_label="high"),
        ],
    )
    tables = {
        "country": pd.DataFrame({"c_id": [1, 2],
                                 "country_name": ["Atlantis", "Wakanda"]}),
        "region": pd.DataFrame({"r_id": [10, 20], "c_ref": [1, 2],
                                "region_name": ["North", "South"]}),
        "district": pd.DataFrame({"d_id": [100, 200], "r_ref": [10, 20],
                                  "district_name": ["D1", "D2"]}),
        "store": pd.DataFrame({"s_id": [1000, 2000], "d_ref": [100, 200]}),
        "sale": pd.DataFrame({"sale_id": [1, 2, 3], "st_ref": [1000, 1000, 2000],
                              "amount": [500, 300, 700]}),
    }
    return contract, tables


_CHAIN_Q = "total sales amount by country"

_CHAIN_FULL_SQL = (
    "SELECT co.country_name, SUM(sa.amount) AS total_amount "
    "FROM sale sa "
    "JOIN store st ON sa.st_ref = st.s_id "
    "JOIN district di ON st.d_ref = di.d_id "
    "JOIN region re ON di.r_ref = re.r_id "
    "JOIN country co ON re.c_ref = co.c_id "
    "GROUP BY co.country_name"
)

# what the model emits when `district` was never shown to it: it cannot bridge
# store→region, so it cross-joins country onto the reachable component → cartesian,
# every country gets the grand total (1500) — the silent wrong number.
_CHAIN_PARTIAL_SQL = (
    "SELECT co.country_name, SUM(sa.amount) AS total_amount "
    "FROM sale sa "
    "JOIN store st ON sa.st_ref = st.s_id "
    "CROSS JOIN country co "
    "GROUP BY co.country_name"
)


class _ChainAwareProvider(MockProvider):
    """Faithful model double: it can only join tables it is actually SHOWN. If the
    bridge `district` is missing from the schema prompt, it emits the partial
    (cross-join) SQL — exactly how a real LLM produces a silent wrong number from a
    truncated schema."""

    def _sql(self, user: str) -> str:
        import json
        if "district" in user:           # full chain visible → correct SQL
            sql = _CHAIN_FULL_SQL
            used = ["sale", "store", "district", "region", "country"]
        else:                            # bridge hidden → partial / cartesian
            sql = _CHAIN_PARTIAL_SQL
            used = ["sale", "store", "country"]
        return json.dumps({"sql": sql, "tables_used": used, "assumptions": [],
                           "needs_clarification": False, "clarifying_question": None})


def test_chain_resolves_all_bridge_tables():
    """GOAL 1: the A↔E question must pull in EVERY bridge — including `district`,
    which sits two hops from both anchors. Coded keys mean no name leakage, so only
    real graph traversal can find it.

    Baseline (current code): the single-pass one-hop loop drops `district` →
    resolved tables = {country, region, store, sale} → FAILS here."""
    contract, _ = _chain()
    rel = nl2sql_mod.select_relevant(_CHAIN_Q, contract)
    for t in ("country", "region", "district", "store", "sale"):
        assert t in rel.tables, f"{t} missing from resolved chain {sorted(rel.tables)}"


def test_chain_bridge_contributes_its_columns():
    """GOAL 1: a bridge pulled in by traversal must contribute the columns the query
    needs — not only its join keys (the bug noted in the audit)."""
    contract, _ = _chain()
    rel = nl2sql_mod.select_relevant(_CHAIN_Q, contract)
    cols = rel.columns.get("district", set())
    # both join keys must be present so the chain can actually be welded together
    assert {"r_ref", "d_id"} <= cols, f"district join keys missing: {sorted(cols)}"


def test_chain_end_to_end_known_answer():
    """GOAL 1 end-to-end: with full traversal the model sees `district`, emits the
    4-join chain, and returns the exact ground truth (Atlantis 800, Wakanda 700).

    Baseline (current code): `district` is dropped from the prompt → the model
    cross-joins → every country shows 1500 → FAILS the number assertion."""
    contract, tables = _chain()
    res = answer(_CHAIN_Q, tables, contract=contract, provider=_ChainAwareProvider())
    assert res.status == "answered", (res.status, res.clarifying_question)
    got = {r["country_name"]: r["total_amount"] for r in res.rows}
    assert got == {"Atlantis": 800, "Wakanda": 700}, got


def test_chain_full_sql_joins_all_five_tables():
    """The emitted SQL for the known-answer chain must reference all five tables —
    proof the JOIN was actually composed, not silently truncated."""
    contract, tables = _chain()
    res = answer(_CHAIN_Q, tables, contract=contract, provider=_ChainAwareProvider())
    assert res.status == "answered"
    used = {t.lower() for t in res.tables_used}
    for t in ("country", "region", "district", "store", "sale"):
        assert t in used, f"{t} absent from tables_used {sorted(used)}"


# ── SCENARIO C — WRONG JOIN-KEY (the silent-0-row case from the live stress test) ─
# The model joins all the right tables but on the WRONG key columns (e.g.
# district.d_id = region.c_ref instead of district.r_ref = region.r_id). It parses,
# references real columns, has an ON clause and joins every table — so it slips past
# the cross-join AND completeness guards — yet matches zero rows. The invalid-join-
# key guard must REFUSE it (never return the empty/wrong answer).

# end of chain mis-keyed: district.d_id (a PK) wrongly equated to region.c_ref
_CHAIN_WRONGKEY_SQL = (
    "SELECT co.country_name, SUM(sa.amount) AS total_amount "
    "FROM sale sa "
    "JOIN store st ON sa.st_ref = st.s_id "
    "JOIN district di ON st.d_ref = di.d_id "
    "JOIN region re ON di.d_id = re.c_ref "          # WRONG: should be di.r_ref = re.r_id
    "JOIN country co ON re.c_ref = co.c_id "
    "GROUP BY co.country_name"
)

# head of chain mis-keyed: sale.st_ref wrongly equated to store.d_ref
_CHAIN_WRONGKEY_HEAD_SQL = (
    "SELECT co.country_name, SUM(sa.amount) AS total_amount "
    "FROM sale sa "
    "JOIN store st ON sa.st_ref = st.d_ref "         # WRONG: should be sa.st_ref = st.s_id
    "JOIN district di ON st.d_ref = di.d_id "
    "JOIN region re ON di.r_ref = re.r_id "
    "JOIN country co ON re.c_ref = co.c_id "
    "GROUP BY co.country_name"
)


def test_wrong_join_key_refuses_not_empty():
    """coded-5 reproduction: all five tables joined, every join has an ON, all
    columns exist — but one join uses the wrong key columns → 0 rows. Must REFUSE,
    not return an empty/wrong answer."""
    contract, tables = _chain()
    mock = MockProvider(nl2sql=lambda q: {
        "sql": _CHAIN_WRONGKEY_SQL,
        "tables_used": ["sale", "store", "district", "region", "country"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    res = answer(_CHAIN_Q, tables, contract=contract, provider=mock)
    assert res.status in ("refused", "blocked"), (res.status, res.rows)
    assert not res.rows                                   # never executed
    assert res.suggestions                                # helpful refusal
    assert "join" in (res.clarifying_question or "").lower()


def test_wrong_join_key_head_refuses():
    """coded-6 reproduction: the mis-keyed join is at the head of the chain instead
    of the tail. Same outcome — refuse, never return rows."""
    contract, tables = _chain()
    mock = MockProvider(nl2sql=lambda q: {
        "sql": _CHAIN_WRONGKEY_HEAD_SQL,
        "tables_used": ["sale", "store", "district", "region", "country"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    res = answer(_CHAIN_Q, tables, contract=contract, provider=mock)
    assert res.status in ("refused", "blocked"), (res.status, res.rows)
    assert not res.rows


def test_correct_join_keys_still_pass():
    """Regression: the guard must NOT false-positive — the correct full-chain SQL
    (every join on a discovered key) still answers with the exact ground truth."""
    contract, tables = _chain()
    mock = MockProvider(nl2sql=lambda q: {
        "sql": _CHAIN_FULL_SQL,
        "tables_used": ["sale", "store", "district", "region", "country"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    res = answer(_CHAIN_Q, tables, contract=contract, provider=mock)
    assert res.status == "answered", (res.status, res.clarifying_question)
    got = {r["country_name"]: r["total_amount"] for r in res.rows}
    assert got == {"Atlantis": 800, "Wakanda": 700}, got


def test_invalid_join_key_guard_unit():
    """Direct guardrail check: with the discovered edges supplied, a wrong-key join
    is blocked with kind='invalid_join_key'; the same SQL with edges omitted (None)
    is allowed — proving the check is opt-in and doesn't touch the other guards."""
    from app.engines import guardrail as gr_mod
    contract, _ = _chain()
    schema = contract.guardrail_schema()
    rels = contract.relationships

    bad = gr_mod.validate_sql(_CHAIN_WRONGKEY_SQL, schema, relationships=rels)
    assert not bad.allowed and bad.kind == "invalid_join_key", (bad.allowed, bad.kind)

    good = gr_mod.validate_sql(_CHAIN_FULL_SQL, schema, relationships=rels)
    assert good.allowed, (good.kind, good.reason)

    # opt-in: no edge set supplied → the wrong-key join is NOT key-checked
    skipped = gr_mod.validate_sql(_CHAIN_WRONGKEY_SQL, schema)
    assert skipped.allowed, (skipped.kind, skipped.reason)
