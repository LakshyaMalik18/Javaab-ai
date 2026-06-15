"""
test_phase3.py — Phase 3 query brain, NORMAL tier (mocked LLM, no network/key).

Covers: provider layer, contract assembly + confidence, lean-prompt assembly,
the fail-loud property, and that EVERY generated SQL is forced through the
guardrail. Deterministic green/red — never touches a real model.
"""
from __future__ import annotations

import pandas as pd
import pytest

from _harness import run_pipeline
from _mock_llm import FlakyProvider, MockProvider

from app.engines import execute as execute_mod
from app.engines import nl2sql as nl2sql_mod
from app.engines import schema_contract as contract_mod
from app.engines.orchestrator import SessionBrain, answer
from app.llm import GeminiProvider, GroqProvider, get_provider
from app.llm.base import LLMConfigError, RateLimitError, extract_json
from app.models import RelationshipEdge, SchemaContract, TableContract, ColumnContract


# ── helpers ───────────────────────────────────────────────────────────────────

def _fixture_tables(name, files=None):
    r = run_pipeline(name, files=files)
    assert not r.raised, r.errors
    return r.tables, r.flags


# ── 1. PROVIDER LAYER ──────────────────────────────────────────────────────────

def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 2} hope that helps') == {"a": 2}
    assert extract_json('{"a": {"b": 3}}') == {"a": {"b": 3}}


def test_get_provider_routes_default_and_privacy():
    assert isinstance(get_provider(), GeminiProvider)
    assert isinstance(get_provider(privacy_mode=True), GroqProvider)


def test_default_model_ids():
    assert "flash-lite" in GeminiProvider().model
    assert GroqProvider().model == "llama-3.3-70b-versatile"


def test_missing_key_raises_config_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMConfigError):
        GeminiProvider().complete_json("s", "u")
    with pytest.raises(LLMConfigError):
        GroqProvider().complete_json("s", "u")


def test_user_supplied_key_is_not_stored_in_env(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    p = GeminiProvider(api_key="sk-user-123")
    assert p._require_key() == "sk-user-123"
    import os
    assert "GEMINI_API_KEY" not in os.environ  # never written anywhere


def test_rate_limit_retry_then_success(monkeypatch):
    monkeypatch.setattr("app.llm.base.time.sleep", lambda *_: None)
    p = FlakyProvider(fail_times=2, payload={"ok": True})
    assert p.complete_json("s", "u", retries=3) == {"ok": True}
    assert p.attempts == 2


def test_rate_limit_exhausted_raises(monkeypatch):
    monkeypatch.setattr("app.llm.base.time.sleep", lambda *_: None)
    p = FlakyProvider(fail_times=10)
    with pytest.raises(RateLimitError):
        p.complete_json("s", "u", retries=2)


# ── 2. CONTRACT ASSEMBLY + CONFIDENCE ──────────────────────────────────────────

def test_contract_assembles_columns_and_relationship():
    tables, flags = _fixture_tables("02_join_pair")
    c = contract_mod.build_contract(tables, MockProvider(), flags=flags)

    assert {t.name for t in c.tables} == {"customers", "orders"}
    orders = c.table("orders")
    assert orders.column("amount").meaning  # LLM meaning attached
    assert orders.column("amount").role == "measure"

    # the headline relationship is present in the contract
    edge = next(
        (e for e in c.relationships
         if {(e.from_table, e.from_col), (e.to_table, e.to_col)}
         == {("orders", "customer_id"), ("customers", "id")}),
        None,
    )
    assert edge is not None
    assert edge.confidence_label == "high"

    # guardrail_schema is well-formed
    gs = c.guardrail_schema()
    assert "customer_id" in gs["orders"] and "id" in gs["customers"]


def test_low_llm_confidence_marks_provisional():
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(label_overrides={
        ("orders", "amount"): {"meaning": "unclear", "confidence": 0.2,
                               "clarifying_question": "Is amount revenue or a code?"},
    })
    c = contract_mod.build_contract(tables, mock, flags=flags)
    amount = c.table("orders").column("amount")
    assert amount.provisional is True
    assert amount.clarifying_question == "Is amount revenue or a code?"


def test_ambiguous_date_flag_forces_provisional():
    # fixture 04: column `date2` is genuinely ambiguous (all fields <= 12)
    tables, flags = _fixture_tables("04_ambiguous_dates")
    c = contract_mod.build_contract(tables, MockProvider(), flags=flags)
    date2 = c.table("data").column("date2")
    assert date2.provisional is True
    assert "DD/MM" in date2.clarifying_question or "format" in date2.clarifying_question


def test_skip_llm_builds_deterministic_contract():
    tables, flags = _fixture_tables("02_join_pair")
    c = contract_mod.build_contract(tables, MockProvider(), flags=flags, skip_llm=True)
    # id column should still score high on the deterministic heuristic
    assert c.table("customers").column("id").confidence >= 0.8


# ── 3. LEAN PROMPT / RELEVANCE ─────────────────────────────────────────────────

def _three_table_contract() -> SchemaContract:
    def col(n, role="dimension"):
        return ColumnContract(name=n, raw_name=n, dtype="text", role=role)
    return SchemaContract(
        tables=[
            TableContract(name="orders", columns=[
                col("order_id", "id"), col("customer_id", "id"), col("amount", "measure")]),
            TableContract(name="customers", columns=[
                col("id", "id"), col("segment"), col("name")]),
            TableContract(name="weather", columns=[
                col("station"), col("temperature", "measure")]),
        ],
        relationships=[RelationshipEdge(
            from_table="orders", from_col="customer_id",
            to_table="customers", to_col="id",
            confidence=0.95, confidence_label="high")],
    )


def test_select_relevant_excludes_unrelated_table():
    c = _three_table_contract()
    rel = nl2sql_mod.select_relevant("total amount by customer segment", c)
    assert "orders" in rel.tables and "customers" in rel.tables
    assert "weather" not in rel.tables  # unrelated → excluded from the prompt


def test_lean_prompt_omits_irrelevant_table_text():
    c = _three_table_contract()
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT 1", "tables_used": ["orders"], "assumptions": [],
        "needs_clarification": False, "clarifying_question": None})
    rel = nl2sql_mod.select_relevant("amount by segment", c)
    nl2sql_mod.generate_sql("amount by segment", c, rel, mock)
    sent = mock.calls_with(nl2sql_mod.SYSTEM_TAG)[0][1]
    assert "weather" not in sent and "temperature" not in sent
    assert "orders" in sent and "segment" in sent


# ── 4. FAIL-LOUD PROPERTY (the distinctive behaviour) ──────────────────────────

def test_fail_loud_unmapped_question_refuses():
    tables, flags = _fixture_tables("02_join_pair")
    # ask something with no mappable column/table at all
    res = answer("What is the profit margin forecast?", tables,
                 provider=MockProvider(), flags=flags)
    assert res.status == "refused"
    assert res.sql is None
    assert res.clarifying_question


def test_fail_loud_provisional_column_asks_clarifying():
    tables, flags = _fixture_tables("04_ambiguous_dates")
    # the question depends on the ambiguous `date2` column → must ASK, not guess
    res = answer("How many events fall on each date2 value?", tables,
                 provider=MockProvider(), flags=flags)
    assert res.status == "clarify"
    assert res.sql is None
    assert "date2" in res.clarifying_question or "format" in res.clarifying_question


def test_fail_loud_model_requests_clarification():
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(nl2sql=lambda q: {
        "sql": None, "tables_used": [], "assumptions": [],
        "needs_clarification": True,
        "clarifying_question": "Which time window do you mean?"})
    res = answer("Show me amount over the period", tables, provider=mock, flags=flags)
    assert res.status == "clarify"
    assert res.clarifying_question == "Which time window do you mean?"


def test_fail_loud_never_fabricates_sql_when_unsure():
    # across all fail-loud paths, SQL is never produced
    tables, flags = _fixture_tables("02_join_pair")
    for q, mock in [
        ("totally unrelated quux blorp", MockProvider()),
        ("amount please", MockProvider(nl2sql=lambda q: {
            "sql": None, "needs_clarification": True,
            "clarifying_question": "?", "tables_used": [], "assumptions": []})),
    ]:
        res = answer(q, tables, provider=mock, flags=flags)
        assert res.status in ("refused", "clarify")
        assert res.sql is None


# ── 4b. BUSINESS-SYNONYM MAPPING (revenue → amount) ────────────────────────────

def _orders_contract_and_tables(extra_money_col: bool = False):
    """A tiny orders schema whose `amount` column sums to the ground-truth
    $19,750.50. With extra_money_col=True a second monetary column is added so the
    synonym 'revenue' becomes genuinely ambiguous."""
    cols = [
        ColumnContract(name="order_id", raw_name="order_id", dtype="numeric", role="id", is_id=True),
        ColumnContract(name="customer_id", raw_name="customer_id", dtype="numeric", role="id", is_id=True, is_fk=True),
        ColumnContract(name="amount", raw_name="amount", dtype="numeric", role="measure", meaning="order amount in USD"),
    ]
    data = {
        "order_id": [1, 2, 3, 4],
        "customer_id": [1, 1, 2, 3],
        "amount": [5000.00, 7500.50, 4250.00, 3000.00],  # sum = 19750.50
    }
    if extra_money_col:
        cols.append(ColumnContract(name="refund_amount", raw_name="refund_amount",
                                   dtype="numeric", role="measure", meaning="refunded amount in USD"))
        data["refund_amount"] = [100.0, 0.0, 50.0, 0.0]
    contract = SchemaContract(tables=[TableContract(name="orders", row_count=4, columns=cols)])
    return contract, {"orders": pd.DataFrame(data)}


def test_business_synonym_revenue_maps_to_amount():
    """'revenue' must resolve to the single monetary column (deterministically),
    and the answer must equal the ground-truth $19,750.50."""
    contract, tables = _orders_contract_and_tables()

    # deterministic relevance: revenue → orders.amount, no ambiguity
    rel = nl2sql_mod.select_relevant("what is the total revenue", contract)
    assert rel.matched_anything
    assert rel.clarify_question is None
    assert "amount" in rel.columns.get("orders", set())

    # end-to-end: model maps revenue→amount, SUMs it, ground truth comes back
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT SUM(amount) AS total_revenue FROM orders",
        "tables_used": ["orders"], "assumptions": ["mapped 'revenue' to the amount column"],
        "needs_clarification": False, "clarifying_question": None})
    res = answer("what is the total revenue", tables, contract=contract, provider=mock)
    assert res.status == "answered"
    assert round(res.rows[0]["total_revenue"], 2) == 19750.50


def test_business_synonym_ambiguous_two_money_columns_clarifies():
    """When the synonym could mean two money columns, fail loud — ask which, never
    guess, and never even call the model."""
    contract, tables = _orders_contract_and_tables(extra_money_col=True)

    rel = nl2sql_mod.select_relevant("what is the total revenue", contract)
    assert rel.clarify_question  # two monetary measures → ambiguous

    def _boom(q):
        raise AssertionError("nl2sql must not run for an ambiguous synonym")

    res = answer("what is the total revenue", tables,
                 contract=contract, provider=MockProvider(nl2sql=_boom))
    assert res.status == "clarify"
    assert res.sql is None
    assert "amount" in res.clarifying_question and "refund_amount" in res.clarifying_question


# ── 5. GUARDRAIL IS ALWAYS IN THE PATH ─────────────────────────────────────────

def test_generated_sql_passes_through_guardrail_when_valid():
    tables, flags = _fixture_tables("02_join_pair")
    metrics_holder = {}
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT COUNT(*) AS n FROM orders",
        "tables_used": ["orders"], "assumptions": [],
        "needs_clarification": False, "clarifying_question": None})
    brain = SessionBrain(tables, provider=mock, flags=flags)
    res = brain.ask("How many orders are there?")
    assert res.status == "answered"
    # guardrail injected a LIMIT and logged an ALLOWED decision
    assert "LIMIT" in res.sql.upper()
    assert brain.metrics.allowed == 1 and brain.metrics.blocked == 0


def test_guardrail_blocks_destructive_generated_sql():
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "DROP TABLE orders", "tables_used": ["orders"], "assumptions": [],
        "needs_clarification": False, "clarifying_question": None})
    brain = SessionBrain(tables, provider=mock, flags=flags)
    # phrased innocuously so the NL-intent gate doesn't fire — this exercises the
    # SQL-level guardrail backstop, which must catch the forced DROP TABLE.
    res = brain.ask("How many orders are there?")
    assert res.status == "blocked"
    assert res.blocked_reason
    assert brain.metrics.blocked == 1  # logged as blocked, never executed


# ── 6. DESTRUCTIVE NL INTENT IS BLOCKED BEFORE SQL GENERATION ───────────────────

def test_destructive_nl_intent_blocks_before_sql():
    tables, flags = _fixture_tables("02_join_pair")
    # a provider that would explode if reached — proves the block is pre-SQL
    def _boom(q):
        raise AssertionError("nl2sql must not be called for destructive intent")
    mock = MockProvider(nl2sql=_boom)
    brain = SessionBrain(tables, provider=mock, flags=flags)
    res = brain.ask("delete all orders")
    assert res.status == "blocked"
    assert res.sql is None
    assert "read-only" in res.blocked_reason.lower()
    assert brain.metrics.blocked == 1  # recorded for the trust panel


def test_destructive_intent_variants_all_block():
    tables, flags = _fixture_tables("02_join_pair")
    for q in [
        "delete all orders",
        "drop the orders table",
        "truncate orders",
        "wipe the database",
        "remove all customers",
        "get rid of everything",
        "erase all the records",
    ]:
        res = answer(q, tables, provider=MockProvider(), flags=flags)
        assert res.status == "blocked", f"{q!r} should be blocked, got {res.status}"
        assert res.sql is None


def test_analytical_questions_are_not_false_positively_blocked():
    tables, flags = _fixture_tables("02_join_pair")
    # these contain destructive-adjacent words but are legitimate questions
    for q in [
        "show me the months where revenue dropped",
        "remove duplicates and count distinct customers",
        "which orders were updated most recently?",
    ]:
        res = answer(q, tables, provider=MockProvider(), flags=flags)
        assert res.status != "blocked", f"{q!r} was wrongly blocked"


def test_guardrail_blocks_unknown_column():
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT nonexistent_col FROM orders", "tables_used": ["orders"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    res = answer("anything about orders amount", tables, provider=mock, flags=flags)
    assert res.status == "blocked"
    assert "unknown column" in res.blocked_reason


# ── 6. END-TO-END (mocked) ─────────────────────────────────────────────────────

def test_answer_end_to_end_aggregate():
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT c.segment, SUM(o.amount) AS total FROM orders o "
               "JOIN customers c ON o.customer_id = c.id GROUP BY c.segment",
        "tables_used": ["orders", "customers"],
        "assumptions": ["'total' = SUM(amount)"],
        "needs_clarification": False, "clarifying_question": None})
    res = answer("total amount by customer segment", tables, provider=mock, flags=flags)
    assert res.status == "answered"
    assert res.insight
    assert res.rows and "segment" in res.columns and "total" in res.columns
    assert res.assumptions == ["'total' = SUM(amount)"]
    assert res.followups


def test_answer_end_to_end_single_table_count():
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT COUNT(*) AS order_count FROM orders",
        "tables_used": ["orders"], "assumptions": [],
        "needs_clarification": False, "clarifying_question": None})
    res = answer("how many orders", tables, provider=mock, flags=flags)
    assert res.status == "answered"
    assert res.rows[0]["order_count"] == len(tables["orders"])


# ── 7. SESSION CACHING + EXECUTION ─────────────────────────────────────────────

def test_schema_labelling_cached_once_per_session():
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT COUNT(*) AS n FROM orders", "tables_used": ["orders"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    brain = SessionBrain(tables, provider=mock, flags=flags)
    brain.ask("how many orders")
    brain.ask("how many orders again")
    from app.engines.schema_ai import SYSTEM_TAG as SCHEMA_TAG
    assert len(mock.calls_with(SCHEMA_TAG)) == 1  # labelled once, reused


def test_run_query_basic():
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    out = execute_mod.run_query({"t": df}, "SELECT SUM(a) AS s FROM t")
    assert out.iloc[0]["s"] == 6
