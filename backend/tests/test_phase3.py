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


def test_guardrail_stops_unknown_column_but_does_not_label_it_blocked():
    """An unknown column is hallucinated SQL: it must FAIL LOUD (never execute,
    never return rows) — but it is NOT destructive intent, so it is routed to the
    "couldn't map" refusal, not the "read-only by design" block."""
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT nonexistent_col FROM orders", "tables_used": ["orders"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    res = answer("anything about orders amount", tables, provider=mock, flags=flags)
    assert res.status == "refused"          # fail loud — not executed
    assert res.blocked_reason is None       # NOT the destructive "read-only" block
    assert not res.rows                      # never returned fabricated rows
    assert "couldn't map" in res.clarifying_question.lower()
    assert res.suggestions                   # helpful: real-schema questions offered


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


# ── 8. BUG FIXES: table-name consistency + fail-loud-but-helpful refusals ───────

def test_table_name_flows_consistently_through_all_layers():
    """Bug 1 regression: a messy filename must produce ONE sanitized table name
    that is identical across DuckDB registration, the contract shown to the model,
    and the guardrail's known-tables — and the model's SQL against that name runs."""
    from app.upload_pipeline import process_upload, safe_table_name
    up = process_upload([("Events 2024.csv", b"id,quantity\n1,10\n2,5\n")])
    registered = set(up.tables)
    contract = contract_mod.build_contract(
        up.tables, GroqProvider(api_key=None), flags=up.flags,
        profiles=up.profiles, relationships=up.relationships, skip_llm=True)
    shown = {t.name for t in contract.tables}
    known = set(contract.guardrail_schema())
    assert registered == shown == known == {"events_2024"}  # never "events"/"events 2024"
    assert safe_table_name("123 Q1!!") == "t_123_q1"          # leading digit + punctuation

    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT SUM(quantity) AS total FROM events_2024", "tables_used": ["events_2024"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    res = answer("total quantity", up.tables, contract=contract, provider=mock)
    assert res.status == "answered"
    assert res.rows[0]["total"] == 15


def test_unknown_table_fails_loud_as_couldnt_map_not_blocked():
    """Hallucinated table (not in schema) must FAIL LOUD: never execute, never
    return rows, and be labelled "couldn't map" — NOT the destructive read-only
    block. This is the test that proves an unmapped table still refuses."""
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "SELECT SUM(quantity) AS total_bonds_sold FROM events",  # 'events' doesn't exist
        "tables_used": ["events"], "assumptions": [],
        "needs_clarification": False, "clarifying_question": None})
    res = answer("how many bonds were sold", tables, provider=mock, flags=flags)
    assert res.status == "refused"            # fail loud — did NOT execute
    assert res.blocked_reason is None         # NOT "read-only by design"
    assert not res.rows                        # no fabricated rows
    assert "couldn't map" in res.clarifying_question.lower()


def test_suggested_questions_returned_on_unmappable_question():
    """Bug 3: an unmappable question returns 3-4 suggestions built from the REAL
    schema (actual table + column names), so the refusal is helpful, not a dead end."""
    tables, flags = _fixture_tables("02_join_pair")
    # gibberish that maps to nothing in the schema
    res = answer("what is the airspeed velocity of an unladen swallow", tables,
                 provider=MockProvider(), flags=flags)
    assert res.status == "refused"
    assert not res.rows
    assert 1 <= len(res.suggestions) <= 4
    # suggestions reference a real table name from the uploaded data
    real_tables = set(tables)
    assert any(any(t in s for t in real_tables) for s in res.suggestions)


def test_destructive_block_still_labelled_read_only():
    """Guard against over-correction: a genuine non-SELECT must STILL be a
    destructive "read-only by design" block, not a "couldn't map" refusal."""
    tables, flags = _fixture_tables("02_join_pair")
    mock = MockProvider(nl2sql=lambda q: {
        "sql": "DROP TABLE orders", "tables_used": ["orders"], "assumptions": [],
        "needs_clarification": False, "clarifying_question": None})
    res = answer("how many orders are there", tables, provider=mock, flags=flags)
    assert res.status == "blocked"
    assert res.blocked_reason
    assert not res.suggestions


# ── 9. INTERPRETATION LAYER (vague terms/values → real columns; net stays loud) ─

_BONDS_CSV = (
    b"Bond Trades Export,,,\n"                  # row 1: junk/banner — must be skipped
    b"bond_id,buy_sell,quantity\n"              # row 2: the real header
    b"BOND1,BUY,100\n"
    b"BOND1,SELL,40\n"
    b"BOND1,SELL,35\n"
    b"BOND2,SELL,10\n"
    b"BOND2,BUY,5\n"
)


def _events_bonds():
    """Real upload (incl. the junk leading row) → (tables, deterministic contract).
    Ground truth: BOND1 SELL quantity = 40+35 = 75; BOND2 SELL = 10; all SELL = 85."""
    from app.upload_pipeline import process_upload
    up = process_upload([("events.csv", _BONDS_CSV)])
    contract = contract_mod.build_contract(
        up.tables, GroqProvider(api_key=None), flags=up.flags,
        profiles=up.profiles, relationships=up.relationships, skip_llm=True)
    return up.tables, contract


def test_junk_leading_row_skipped_real_header_detected():
    """The bonds file's row 1 is junk (sparse banner); the real header is row 2.
    Ingestion must skip the junk and read the true columns."""
    tables, _ = _events_bonds()
    df = tables["events"]
    assert set(df.columns) == {"bond_id", "buy_sell", "quantity"}
    assert "bond trades export" not in " ".join(df.columns).lower()
    assert len(df) == 5  # 5 trade rows, no junk/header leakage


_BONDS_REF_CSV = (
    b",,,\n"                                            # row 1: junk (,,,) — skip it
    b"BondID,Coupon,Frequency,MonthsSinceCoupon\n"      # row 2: the real header
    b"BOND1,5.0,2,3\n"
    b"BOND2,4.5,2,1\n"
)


def test_leading_junk_row_bonds_header_parsed_from_row_two():
    """The tester's bonds file has a junk first row (,,,) before the real header.
    Ingest must skip it and read BondID/Coupon/Frequency/MonthsSinceCoupon."""
    from app.engines.ingest import ingest_csv
    out = ingest_csv(_BONDS_REF_CSV, "bonds")
    assert out["raw_headers"] == ["BondID", "Coupon", "Frequency", "MonthsSinceCoupon"]
    assert set(out["df"].columns) == {"bondid", "coupon", "frequency", "monthssincecoupon"}
    assert len(out["df"]) == 2  # two bond rows, junk row gone


# helper: a confident, valid mapping the AI would propose for "how many bond1 sold"
def _bond1_sold_mapping(_q):
    return {
        "tables": ["events"], "columns": ["quantity"],
        "filters": [{"column": "bond_id", "op": "=", "value": "BOND1"},
                    {"column": "buy_sell", "op": "=", "value": "SELL"}],
        "aggregation": "SUM", "measure": "quantity", "group_by": [],
        "confidence": "high", "alternatives": [], "unmappable": False,
        "reason": "bond1->BondID='BOND1', sold->BuySell='SELL'"}


def test_tier2_interprets_value_question_and_returns_ground_truth_75():
    """Tier 1 misses "how many bond1 sold"; Tier 2 proposes a structured mapping
    (bond1→BondID='BOND1', sold→BuySell='SELL', SUM quantity). The engine validates
    it, runs the SQL, and returns the EXACT ground truth 40+35 = 75 — with the
    interpretation surfaced for transparency."""
    from app.engines.nl2sql import SYSTEM_TAG as NL2SQL_TAG
    from app.engines.interpret import SYSTEM_TAG as INTERPRET_TAG
    tables, contract = _events_bonds()

    # Tier 1 genuinely misses (value-based phrasing) → Tier 2 fires
    assert nl2sql_mod.select_relevant("how many bond1 sold", contract).matched_anything is False

    mock = MockProvider(
        mapping=_bond1_sold_mapping,
        nl2sql=lambda q: {
            "sql": "SELECT SUM(quantity) AS total_sold FROM events "
                   "WHERE bond_id = 'BOND1' AND buy_sell = 'SELL'",
            "tables_used": ["events"], "assumptions": [],
            "needs_clarification": False, "clarifying_question": None})
    res = answer("how many bond1 sold", tables, contract=contract, provider=mock)

    assert res.status == "answered"
    assert res.rows[0]["total_sold"] == 75            # EXACT ground truth
    # transparency: the engine's vetted interpretation is shown in assumptions
    assert any("Read as:" in a and "bond_id" in a and "buy_sell" in a for a in res.assumptions)
    # the interpretation step actually saw the real columns + sample values
    map_prompt = mock.calls_with(INTERPRET_TAG)[0][1]
    assert "bond_id" in map_prompt and "buy_sell" in map_prompt
    assert "BOND1" in map_prompt and "SELL" in map_prompt
    assert mock.calls_with(NL2SQL_TAG)  # generation ran after the mapping was vetted


def test_tier2_sell_percentage_interprets_buysell_correctly():
    """A "what percentage were sold" question maps BuySell correctly. Ground truth:
    SELL quantity = 40+35+10 = 85 of total 100+40+35+10+5 = 190 → 44.74%."""
    tables, contract = _events_bonds()
    mapping = {
        "tables": ["events"], "columns": ["buy_sell", "quantity"], "filters": [],
        "aggregation": "SUM", "measure": "quantity", "group_by": [],
        "confidence": "high", "alternatives": [], "unmappable": False,
        "reason": "share of quantity where buy_sell='SELL'"}
    mock = MockProvider(
        mapping=mapping,
        nl2sql=lambda q: {
            "sql": "SELECT ROUND(100.0 * SUM(CASE WHEN buy_sell = 'SELL' THEN quantity "
                   "ELSE 0 END) / SUM(quantity), 2) AS sell_pct FROM events",
            "tables_used": ["events"], "assumptions": ["computed SELL share of quantity"],
            "needs_clarification": False, "clarifying_question": None})
    res = answer("what percentage of bonds were sold", tables, contract=contract, provider=mock)
    assert res.status == "answered"
    assert res.rows[0]["sell_pct"] == 44.74        # EXACT ground truth


def test_tier2_ambiguous_returns_clarify_and_runs_no_sql():
    """Low confidence / two plausible readings → the engine must ASK, never let the
    LLM silently pick, and NO SQL may run."""
    from app.engines.nl2sql import SYSTEM_TAG as NL2SQL_TAG
    tables, contract = _events_bonds()
    ambiguous_mapping = {
        "tables": ["events"], "columns": [], "filters": [],
        "aggregation": None, "measure": None, "group_by": [],
        "confidence": "low",
        "alternatives": [{"term": "sold", "options": ["buy_sell", "trade_state"]}],
        "unmappable": False, "reason": "'sold' is ambiguous"}

    def _boom(q):
        raise AssertionError("nl2sql/generation must NOT run for an ambiguous mapping")

    mock = MockProvider(mapping=ambiguous_mapping, nl2sql=_boom)
    res = answer("how many were sold", tables, contract=contract, provider=mock)
    assert res.status == "clarify"
    assert "sold" in res.clarifying_question and "buy_sell" in res.clarifying_question
    assert not res.rows                                   # nothing ran
    assert not mock.calls_with(NL2SQL_TAG)               # generation never happened


def test_tier2_hallucinated_mapping_caught_by_revalidation_not_executed():
    """If the AI proposes a column that doesn't exist, re-validation rejects it
    BEFORE generation — it never reaches SQL or execution."""
    from app.engines.nl2sql import SYSTEM_TAG as NL2SQL_TAG
    tables, contract = _events_bonds()
    hallucinated = {
        "tables": ["events"], "columns": ["notional"],   # 'notional' is not a real column
        "filters": [{"column": "bond_id", "op": "=", "value": "BOND1"}],
        "aggregation": "SUM", "measure": "notional", "group_by": [],
        "confidence": "high", "alternatives": [], "unmappable": False, "reason": "x"}

    def _boom(q):
        raise AssertionError("generation must NOT run for a hallucinated mapping")

    mock = MockProvider(mapping=hallucinated, nl2sql=_boom)
    res = answer("total notional for bond1", tables, contract=contract, provider=mock)
    assert res.status == "refused"                        # fail loud
    assert res.blocked_reason is None                     # not a destructive block
    assert not res.rows                                   # never executed
    assert not mock.calls_with(NL2SQL_TAG)               # rejected before generation


def test_tier2_hallucinated_filter_value_caught_by_revalidation():
    """A value-level filter whose value doesn't exist in the column is also caught
    by re-validation (value-existence check) and never executed."""
    tables, contract = _events_bonds()
    bad_value = {
        "tables": ["events"], "columns": ["quantity"],
        "filters": [{"column": "bond_id", "op": "=", "value": "BOND9"}],  # no BOND9 in data
        "aggregation": "SUM", "measure": "quantity", "group_by": [],
        "confidence": "high", "alternatives": [], "unmappable": False, "reason": "x"}
    res = answer("how many bond9 sold", tables, contract=contract,
                 provider=MockProvider(mapping=bad_value, nl2sql=lambda q: {"sql": "x"}))
    assert res.status == "refused"
    assert not res.rows
    assert "BOND9" in (res.clarifying_question or "")     # tells the user what was missing


def test_tier2_unmappable_concept_fails_loud_with_suggestions():
    """A concept with no matching column/value at all (profit, no cost column) →
    the AI marks it unmappable, we refuse helpfully, nothing runs."""
    from app.engines.nl2sql import SYSTEM_TAG as NL2SQL_TAG
    tables, contract = _events_bonds()
    unmappable = {"tables": [], "columns": [], "filters": [], "aggregation": None,
                  "measure": None, "group_by": [], "confidence": "low",
                  "alternatives": [], "unmappable": True,
                  "reason": "no cost column exists, profit cannot be derived"}
    mock = MockProvider(mapping=unmappable, nl2sql=lambda q: {"sql": "x"})
    res = answer("what is the total profit", tables, contract=contract, provider=mock)
    assert res.status == "refused"
    assert not res.rows
    assert res.suggestions and any("events" in s for s in res.suggestions)
    assert not mock.calls_with(NL2SQL_TAG)


def test_tier2_known_answer_batch_on_events_data():
    """Ground-truth batch through the full Tier-2 path (valid mapping → generation →
    execution). Asserts EXACT numbers, guarding against a wrong-column interpretation
    giving a plausible-wrong answer."""
    tables, contract = _events_bonds()
    valid_mapping = {
        "tables": ["events"], "columns": ["bond_id", "buy_sell", "quantity"],
        "filters": [], "aggregation": "SUM", "measure": "quantity", "group_by": [],
        "confidence": "high", "alternatives": [], "unmappable": False, "reason": "ok"}
    cases = [
        ("SELECT SUM(quantity) AS v FROM events WHERE bond_id='BOND1' AND buy_sell='SELL'", 75),
        ("SELECT SUM(quantity) AS v FROM events WHERE buy_sell='SELL'", 85),
        ("SELECT SUM(quantity) AS v FROM events WHERE bond_id='BOND2'", 15),
        ("SELECT COUNT(*) AS v FROM events WHERE buy_sell='BUY'", 2),
    ]
    for sql, expected in cases:
        mock = MockProvider(mapping=valid_mapping, nl2sql=lambda q, _sql=sql: {
            "sql": _sql, "tables_used": ["events"], "assumptions": [],
            "needs_clarification": False, "clarifying_question": None})
        res = answer("ground truth question", tables, contract=contract, provider=mock)
        assert res.status == "answered", (sql, res.status, res.clarifying_question)
        assert res.rows[0]["v"] == expected, (sql, res.rows[0]["v"], expected)


def test_tier2_clarify_carries_proposed_action_and_affirmative_runs_it():
    """Bug 3a: an ambiguous clarify carries `proposed_action` (the AI's best-guess
    concrete question). Affirming it = re-asking that exact question on a fresh,
    stateless /ask, which resolves and returns the ground truth (SELL qty = 85)."""
    tables, contract = _events_bonds()
    PROPOSED = "total quantity where buy_sell is SELL"

    def _mapping(q):
        if q == PROPOSED:  # the affirmative re-ask → confident, unambiguous
            return {"tables": ["events"], "columns": ["quantity"],
                    "filters": [{"column": "buy_sell", "op": "=", "value": "SELL"}],
                    "aggregation": "SUM", "measure": "quantity", "group_by": [],
                    "confidence": "high", "alternatives": [], "unmappable": False,
                    "reason": "ok"}
        # the original vague question → ambiguous, with a best-guess proposal
        return {"tables": ["events"], "columns": [], "filters": [],
                "aggregation": None, "measure": None, "group_by": [],
                "confidence": "low",
                "alternatives": [{"term": "sold", "options": ["buy_sell", "trade_state"]}],
                "unmappable": False, "reason": "'sold' is ambiguous",
                "proposed_question": PROPOSED}

    mock = MockProvider(mapping=_mapping, nl2sql=lambda q: {
        "sql": "SELECT SUM(quantity) AS v FROM events WHERE buy_sell = 'SELL'",
        "tables_used": ["events"], "assumptions": [],
        "needs_clarification": False, "clarifying_question": None})

    # turn 1: vague → clarify carrying the concrete proposed_action, no SQL runs
    clarify = answer("how many were sold", tables, contract=contract, provider=mock)
    assert clarify.status == "clarify"
    assert clarify.proposed_action == PROPOSED
    assert not clarify.rows

    # turn 2: the "Yes — run it" affirmative re-asks proposed_action → answered
    affirmed = answer(clarify.proposed_action, tables, contract=contract, provider=mock)
    assert affirmed.status == "answered"
    assert affirmed.rows[0]["v"] == 85


def test_confident_tier2_answer_has_no_proposed_action():
    """A confident Tier-2 answer is not a clarify — it must not carry a chip."""
    tables, contract = _events_bonds()
    res = answer("how many bond1 sold", tables, contract=contract,
                 provider=MockProvider(mapping=_bond1_sold_mapping, nl2sql=lambda q: {
                     "sql": "SELECT SUM(quantity) AS v FROM events WHERE bond_id='BOND1' AND buy_sell='SELL'",
                     "tables_used": ["events"], "assumptions": [],
                     "needs_clarification": False, "clarifying_question": None}))
    assert res.status == "answered"
    assert res.proposed_action is None
