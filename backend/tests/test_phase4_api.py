"""
test_phase4_api.py — Phase 4 web layer, NORMAL tier (mocked LLM, no network/key).

Exercises the FastAPI surface end-to-end with TestClient:
  - upload → schema → ask happy path
  - the fail-loud paths through the API (refuse / clarify)
  - guardrail blocking through the API
  - provider rate-limit surfaced as a clean 429 (never a 500)
  - privacy-mode routing + BYO key never stored
  - the explicit ephemeral wipe: nothing survives after a session closes
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from _mock_llm import MockProvider

from app.engines.nl2sql import SYSTEM_TAG as NL2SQL_TAG
from app.llm import FallbackProvider, GeminiProvider, GroqProvider
from app.llm.base import RateLimitError
from app.main import create_app
from app.session import IN_MEMORY, _default_provider_factory

FIX = Path(__file__).parent / "fixtures_audit"


# ── helpers ───────────────────────────────────────────────────────────────────

def _upload_files(*relpaths):
    parts = []
    for rp in relpaths:
        p = FIX / rp
        parts.append(("files", (p.name, p.read_bytes(), "text/csv")))
    return parts


def _client(nl2sql=None, label_overrides=None):
    """A TestClient whose sessions get a deterministic MockProvider."""
    app = create_app()
    app.state.store.provider_factory = lambda **kw: MockProvider(
        nl2sql=nl2sql, label_overrides=label_overrides or {}
    )
    return app, TestClient(app)


def _new_session(client, **body):
    r = client.post("/session", json=body)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


_JOIN_SQL = {
    "sql": "SELECT c.segment, SUM(o.amount) AS total FROM orders o "
           "JOIN customers c ON o.customer_id = c.id GROUP BY c.segment",
    "tables_used": ["orders", "customers"],
    "assumptions": ["'total' = SUM(amount)"],
    "needs_clarification": False, "clarifying_question": None,
}


# ── 1. HAPPY PATH: upload → schema → ask ────────────────────────────────────────

def test_happy_path_upload_schema_ask():
    app, client = _client(nl2sql=lambda q: _JOIN_SQL)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}

    # upload
    up = client.post("/upload", files=_upload_files("02_join_pair/customers.csv",
                                                    "02_join_pair/orders.csv"), headers=h)
    assert up.status_code == 200, up.text
    body = up.json()
    names = {t["name"] for t in body["tables"]}
    assert {"customers", "orders"} <= names
    assert body["ledger"]["total_cells_affected"] >= 0
    assert isinstance(body["ledger"]["records"], list)

    # schema: contract + the headline relationship
    sc = client.get("/schema", headers=h)
    assert sc.status_code == 200, sc.text
    contract = sc.json()
    orders = next(t for t in contract["tables"] if t["name"] == "orders")
    amount = next(c for c in orders["columns"] if c["name"] == "amount")
    assert amount["meaning"]  # LLM meaning attached
    rels = {(r["from_table"], r["from_col"], r["to_table"], r["to_col"]) for r in contract["relationships"]}
    assert ("orders", "customer_id", "customers", "id") in rels

    # ask
    ans = client.post("/ask", json={"question": "total amount by customer segment"}, headers=h)
    assert ans.status_code == 200, ans.text
    a = ans.json()
    assert a["status"] == "answered"
    assert a["insight"]
    assert a["rows"] and "segment" in a["columns"] and "total" in a["columns"]
    assert a["followups"]
    assert "LIMIT" in a["sql"].upper()
    assert a["chart_hint"] in ("bar", "table", "line", "single_value")


# ── 2. FAIL-LOUD through the API ─────────────────────────────────────────────────

def test_fail_loud_refused_through_api():
    # A genuinely-absent concept (no cost/profit column) now fails loud via the
    # interpretation layer: the model is given the full schema, can't map it, and
    # declines — the orchestrator turns that into a helpful "couldn't map" refusal.
    def _decline(q):
        return {"sql": None, "tables_used": [], "assumptions": [],
                "needs_clarification": True,
                "clarifying_question": "There's no cost column, so I can't compute profit margin."}
    app, client = _client(nl2sql=_decline)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=_upload_files("02_join_pair/customers.csv",
                                               "02_join_pair/orders.csv"), headers=h)
    ans = client.post("/ask", json={"question": "What is the profit margin forecast?"}, headers=h)
    assert ans.status_code == 200, ans.text
    a = ans.json()
    assert a["status"] == "refused"
    assert a["sql"] is None
    assert a["clarifying_question"]
    assert a["suggestions"]  # helpful: real-schema questions offered


def test_fail_loud_clarify_provisional_through_api():
    app, client = _client()
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=_upload_files("04_ambiguous_dates/data.csv"), headers=h)
    ans = client.post("/ask", json={"question": "How many events fall on each date2 value?"}, headers=h)
    assert ans.status_code == 200, ans.text
    a = ans.json()
    assert a["status"] == "clarify"
    assert a["sql"] is None
    assert "date2" in a["clarifying_question"] or "format" in a["clarifying_question"]


# ── 3. GUARDRAIL through the API ─────────────────────────────────────────────────

def test_guardrail_blocks_destructive_through_api():
    destructive = {"sql": "DROP TABLE orders", "tables_used": ["orders"],
                   "assumptions": [], "needs_clarification": False, "clarifying_question": None}
    app, client = _client(nl2sql=lambda q: destructive)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=_upload_files("02_join_pair/customers.csv",
                                               "02_join_pair/orders.csv"), headers=h)
    ans = client.post("/ask", json={"question": "delete the orders table"}, headers=h)
    assert ans.status_code == 200, ans.text
    a = ans.json()
    assert a["status"] == "blocked"
    assert a["blocked_reason"]

    # the block is reflected in the live metrics (100% destructive blocked)
    m = client.get("/metrics", headers=h).json()
    assert m["session"]["guardrail_blocked"] == 1
    assert m["session"]["destructive_blocked_pct"] == 100.0


# ── 4. RATE LIMIT → clean 429 (not 500) ──────────────────────────────────────────

class _RateLimitedSQL(MockProvider):
    """Labels fine, but rate-limits every nl2sql call."""

    def _raw_complete(self, system, user, *, max_tokens):
        from app.engines.nl2sql import SYSTEM_TAG as NL
        if NL in system:
            raise RateLimitError("simulated 429")
        return super()._raw_complete(system, user, max_tokens=max_tokens)


def test_rate_limit_surfaced_as_429(monkeypatch):
    monkeypatch.setattr("app.llm.base.time.sleep", lambda *_: None)
    app = create_app()
    app.state.store.provider_factory = lambda **kw: _RateLimitedSQL()
    client = TestClient(app)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=_upload_files("02_join_pair/customers.csv",
                                               "02_join_pair/orders.csv"), headers=h)
    ans = client.post("/ask", json={"question": "total amount by customer segment"}, headers=h)
    assert ans.status_code == 429, ans.text
    assert ans.json()["error"] == "rate_limited"


# ── 4b. AUTOMATIC PROVIDER FALLBACK (rate-limit) ─────────────────────────────────

class _NamedMock(MockProvider):
    def __init__(self, name, **kw):
        super().__init__(**kw)
        self.name = name


class _RateLimitedNL(_NamedMock):
    """Labels fine, but rate-limits every nl2sql call (simulating a busy primary)."""

    def _raw_complete(self, system, user, *, max_tokens):
        if NL2SQL_TAG in system:
            raise RateLimitError("simulated 429")
        return super()._raw_complete(system, user, max_tokens=max_tokens)


def test_provider_routing_enforces_privacy_rule():
    """The privacy rule lives in provider construction: default mode wraps a Groq
    fallback; Privacy Mode is bare Groq with NO fallback wrapper."""
    default = _default_provider_factory(privacy_mode=False)
    assert isinstance(default, FallbackProvider)
    assert isinstance(default.primary, GeminiProvider)
    assert isinstance(default.fallback, GroqProvider)

    privacy = _default_provider_factory(privacy_mode=True)
    assert not isinstance(privacy, FallbackProvider)
    assert isinstance(privacy, GroqProvider)


def test_default_mode_falls_back_to_groq_on_429(monkeypatch):
    monkeypatch.setattr("app.llm.base.time.sleep", lambda *_: None)
    gemini = _RateLimitedNL("gemini")  # primary: labels ok, nl2sql 429s
    groq = _NamedMock("groq", nl2sql=lambda q: {
        "sql": "SELECT COUNT(*) AS n FROM orders", "tables_used": ["orders"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    fb = FallbackProvider(gemini, groq)

    app = create_app()
    app.state.store.provider_factory = lambda **kw: fb
    client = TestClient(app)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=_upload_files("02_join_pair/customers.csv",
                                               "02_join_pair/orders.csv"), headers=h)

    a = client.post("/ask", json={"question": "how many orders"}, headers=h).json()
    assert a["status"] == "answered"            # user never sees the 429
    assert a["provider_used"] == "groq"         # answer came via the fallback
    assert a["fallback_note"]
    assert groq.calls_with(NL2SQL_TAG)          # Groq actually generated the SQL


def test_privacy_mode_does_not_fall_back(monkeypatch):
    monkeypatch.setattr("app.llm.base.time.sleep", lambda *_: None)
    # the opted-out provider — must NEVER be touched in privacy mode
    gemini = _NamedMock("gemini", nl2sql=lambda q: {
        "sql": "SELECT 1", "tables_used": [], "assumptions": [],
        "needs_clarification": False, "clarifying_question": None})
    # the privacy provider (Groq) is rate-limited
    groq = _RateLimitedNL("groq")

    app = create_app()
    # privacy mode returns the BARE Groq provider — no fallback wrapper
    app.state.store.provider_factory = lambda *, privacy_mode=False, user_key=None: groq
    client = TestClient(app)
    sid = _new_session(client, privacy_mode=True)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=_upload_files("02_join_pair/customers.csv",
                                               "02_join_pair/orders.csv"), headers=h)

    r = client.post("/ask", json={"question": "how many orders"}, headers=h)
    assert r.status_code == 429                  # clean "busy", not a 500
    body = r.json()
    assert body["error"] == "rate_limited"
    assert "busy" in body["detail"].lower()
    assert gemini.calls == []                    # the opted-out provider was never consulted


# ── 5. PRIVACY MODE + BYO key (default factory, no override) ──────────────────────

def test_privacy_mode_routes_groq_and_default_routes_gemini():
    app = create_app()              # real default provider_factory
    client = TestClient(app)
    default = client.post("/session", json={}).json()
    privacy = client.post("/session", json={"privacy_mode": True}).json()
    assert default["provider"] == "gemini"
    assert privacy["provider"] == "groq"


def test_byo_key_never_stored_in_env():
    import os
    app = create_app()
    client = TestClient(app)
    secret = "sk-user-should-not-persist-123"
    client.post("/session", json={"privacy_mode": True, "user_key": secret})
    assert os.environ.get("GROQ_API_KEY") != secret
    assert secret not in os.environ.values()


# ── 6. EPHEMERAL WIPE — the privacy mechanism ─────────────────────────────────────

def test_ephemeral_wipe_destroys_everything():
    app, client = _client(nl2sql=lambda q: {
        "sql": "SELECT COUNT(*) AS n FROM orders", "tables_used": ["orders"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None})
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=_upload_files("02_join_pair/customers.csv",
                                               "02_join_pair/orders.csv"), headers=h)
    client.post("/ask", json={"question": "how many orders"}, headers=h)

    # hold a direct reference so we can prove the data is gone after close
    sess = app.state.store._sessions[sid]
    assert sess.tables and sess.contract is not None      # populated before wipe
    assert sess.db_location == IN_MEMORY                  # never a disk file

    # explicit wipe
    r = client.delete("/session", headers=h)
    assert r.status_code == 200 and r.json()["wiped"] is True

    # the session is gone from the store
    assert sid not in app.state.store._sessions
    # everything user-derived is cleared
    assert sess.closed is True
    assert sess.tables == {}
    assert sess.contract is None
    assert sess._user_key is None
    # the in-memory DuckDB connection is closed — any use now raises
    with pytest.raises(Exception):
        sess._con.execute("SELECT 1")
    # subsequent API calls 404
    assert client.get("/schema", headers=h).status_code == 404
    assert client.post("/ask", json={"question": "x"}, headers=h).status_code == 404


def test_idle_timeout_wipes_session():
    app, client = _client(nl2sql=lambda q: _JOIN_SQL)
    sid = _new_session(client)
    sess = app.state.store._sessions[sid]
    sess.timeout_seconds = -1  # force immediate expiry
    # next access detects expiry, wipes, and 404s
    assert client.get("/schema", headers={"X-Session-Id": sid}).status_code == 404
    assert sess.closed is True
    assert sid not in app.state.store._sessions


# ── 6b. RESOLVE DUPLICATES — removal actually reaches query results ──────────────

_COUNT_SQL = {
    "sql": "SELECT COUNT(*) AS n FROM sales",
    "tables_used": ["sales"],
    "assumptions": [],
    "needs_clarification": False,
    "clarifying_question": None,
}

_DUPE_CSV = (
    b"name,amount\n"
    b"Acme,100\n"
    b"Beta,200\n"
    b"Acme,100\n"
    b"Gamma,300\n"
    b"Acme,100\n"
)


def test_resolve_duplicates_removes_rows_from_query_results():
    """End-to-end: a 'remove' decision drops the chosen duplicate rows from the
    session's real data, so a later COUNT reflects the removal — proving it's wired
    through DuckDB, not just local UI state. 'keep' removes nothing."""
    app, client = _client(nl2sql=lambda q: _COUNT_SQL)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}

    up = client.post("/upload", files=[("files", ("sales.csv", _DUPE_CSV, "text/csv"))], headers=h)
    assert up.status_code == 200, up.text
    exact = next(f for f in up.json()["flags"] if f["kind"] == "exact_duplicate")
    group = exact["groups"][0]                 # [0, 2, 4] — three identical Acme rows
    assert len(group) == 3

    # baseline: all five rows are present
    a0 = client.post("/ask", json={"question": "how many rows in sales"}, headers=h).json()
    assert a0["status"] == "answered"
    assert a0["rows"][0]["n"] == 5

    # remove the duplicates: keep the first Acme (index 0), drop the other two
    rd = client.post(
        "/resolve-duplicates",
        json={"decisions": [{"table": "sales", "row_indices": group, "action": "remove"}]},
        headers=h,
    )
    assert rd.status_code == 200, rd.text
    assert rd.json()["removed_rows"] == 2
    assert next(t["row_count"] for t in rd.json()["tables"] if t["name"] == "sales") == 3

    # a LATER query now reflects the removal — the duplicate rows are genuinely gone
    a1 = client.post("/ask", json={"question": "how many rows in sales now"}, headers=h).json()
    assert a1["status"] == "answered"
    assert a1["rows"][0]["n"] == 3

    # a 'keep' decision (and the default) never removes anything
    rd2 = client.post(
        "/resolve-duplicates",
        json={"decisions": [{"table": "sales", "row_indices": [0, 1], "action": "keep"}]},
        headers=h,
    )
    assert rd2.status_code == 200, rd2.text
    assert rd2.json()["removed_rows"] == 0
    a2 = client.post("/ask", json={"question": "still how many in sales"}, headers=h).json()
    assert a2["rows"][0]["n"] == 3


# ── 6c. MANUAL JOIN — persists, becomes active, and answers a query ──────────────

_MANUAL_JOIN_SQL = {
    "sql": "SELECT d.budget, s.name FROM staff s JOIN depts d ON s.team = d.k",
    "tables_used": ["staff", "depts"],
    "assumptions": [],
    "needs_clarification": False,
    "clarifying_question": None,
}

# Dissimilar column names ('k' vs 'team') → value-based discovery deliberately
# misses this join, so it's the perfect case for a user-defined manual join.
_DEPTS_CSV = b"k,budget\nENG,100\nSALES,200\nOPS,300\n"
_STAFF_CSV = b"name,team\nAlice,ENG\nBob,SALES\nCarol,ENG\nDan,OPS\n"


def test_manual_join_persists_becomes_active_and_is_used_in_query():
    """A user-defined join the auto-discovery missed: before defining it, a question
    needing the join fails loud (the join key isn't a known relationship, so the
    guardrail rejects it); after defining it, the SAME question answers — proving the
    manual edge flows into active_relationships() and the live query path."""
    app, client = _client(nl2sql=lambda q: _MANUAL_JOIN_SQL)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}

    up = client.post(
        "/upload",
        files=[
            ("files", ("depts.csv", _DEPTS_CSV, "text/csv")),
            ("files", ("staff.csv", _STAFF_CSV, "text/csv")),
        ],
        headers=h,
    )
    assert up.status_code == 200, up.text

    # auto-discovery missed the join (dissimilar names) → contract has no relationship
    sc = client.get("/schema", headers=h).json()
    assert sc["relationships"] == []

    q = {"question": "show budget by team for staff and depts"}

    # BEFORE: the join key isn't a discovered relationship → guardrail fails it loud
    before = client.post("/ask", json=q, headers=h).json()
    assert before["status"] == "refused"
    assert not before["rows"]

    # define the manual join — it must validate, persist, and become the active link
    cs = client.post(
        "/confirm-schema",
        json={
            "manual_relationships": [
                {"from_table": "staff", "from_col": "team", "to_table": "depts", "to_col": "k"}
            ]
        },
        headers=h,
    )
    assert cs.status_code == 200, cs.text
    active = [r for r in cs.json()["relationships"] if r["active"]]
    assert len(active) == 1  # exactly one active link for the pair
    a = active[0]
    assert (a["from_table"], a["from_col"], a["to_table"], a["to_col"]) == (
        "staff", "team", "depts", "k",
    )

    # AFTER: the identical question now ANSWERS, using the manual join
    after = client.post("/ask", json=q, headers=h).json()
    assert after["status"] == "answered", after
    assert after["rows"]
    assert "budget" in after["columns"]

    # an INVALID manual join (column doesn't exist) is rejected — nothing persisted
    bad = client.post(
        "/confirm-schema",
        json={
            "manual_relationships": [
                {"from_table": "staff", "from_col": "nope", "to_table": "depts", "to_col": "k"}
            ]
        },
        headers=h,
    )
    assert bad.status_code == 400, bad.text


# ── 6d. CUSTOM CLEANING RULES — each type changes the cleaned output ─────────────

# "feline" won't fuzzy-merge with "cat" automatically, and 9999 is a real number —
# so each effect below is attributable to the user's rule, not the auto engine.
_ZOO_CSV = b"animal,score\ncat,10\nfeline,9999\ncat,30\n"


def test_apply_rule_null_token_reduces_nonnull_count():
    count_sql = {
        "sql": "SELECT COUNT(score) AS n FROM zoo", "tables_used": ["zoo"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None,
    }
    app, client = _client(nl2sql=lambda q: count_sql)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=[("files", ("zoo.csv", _ZOO_CSV, "text/csv"))], headers=h)

    a0 = client.post("/ask", json={"question": "count score in zoo"}, headers=h).json()
    assert a0["status"] == "answered" and a0["rows"][0]["n"] == 3

    rr = client.post(
        "/apply-rules",
        json={"rules": [{"type": "null_token", "column": "score", "params": {"value": "9999"}}]},
        headers=h,
    )
    assert rr.status_code == 200, rr.text

    a1 = client.post("/ask", json={"question": "count score in zoo"}, headers=h).json()
    assert a1["rows"][0]["n"] == 2  # the 9999 row is now NULL → not counted


def test_apply_rule_force_type_changes_dtype():
    app, client = _client()
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=[("files", ("zoo.csv", _ZOO_CSV, "text/csv"))], headers=h)

    def _score_dtype():
        sc = client.get("/schema", headers=h).json()
        zoo = next(t for t in sc["tables"] if t["name"] == "zoo")
        return next(c for c in zoo["columns"] if c["name"] == "score")["dtype"]

    assert _score_dtype() == "numeric"  # inferred
    rr = client.post(
        "/apply-rules",
        json={"rules": [{"type": "force_type", "column": "score", "params": {"dtype": "text"}}]},
        headers=h,
    )
    assert rr.status_code == 200, rr.text
    assert _score_dtype() == "text"  # forced, and NOT re-numified by the finalise pass


def test_apply_rule_merge_values_collapses_groups():
    distinct_sql = {
        "sql": "SELECT COUNT(DISTINCT animal) AS n FROM zoo", "tables_used": ["zoo"],
        "assumptions": [], "needs_clarification": False, "clarifying_question": None,
    }
    app, client = _client(nl2sql=lambda q: distinct_sql)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=[("files", ("zoo.csv", _ZOO_CSV, "text/csv"))], headers=h)

    a0 = client.post("/ask", json={"question": "distinct animal in zoo"}, headers=h).json()
    assert a0["rows"][0]["n"] == 2  # cat, feline

    rr = client.post(
        "/apply-rules",
        json={"rules": [{"type": "merge_values", "column": "animal",
                         "params": {"from": ["feline"], "to": "cat"}}]},
        headers=h,
    )
    assert rr.status_code == 200, rr.text

    a1 = client.post("/ask", json={"question": "distinct animal in zoo"}, headers=h).json()
    assert a1["rows"][0]["n"] == 1  # feline collapsed into cat


def test_apply_rule_invalid_is_rejected_and_changes_nothing():
    app, client = _client()
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=[("files", ("zoo.csv", _ZOO_CSV, "text/csv"))], headers=h)

    bad = client.post(
        "/apply-rules",
        json={"rules": [{"type": "force_type", "column": "score", "params": {"dtype": "banana"}}]},
        headers=h,
    )
    assert bad.status_code == 400, bad.text
    # nothing applied — the column is still numeric
    sc = client.get("/schema", headers=h).json()
    zoo = next(t for t in sc["tables"] if t["name"] == "zoo")
    assert next(c for c in zoo["columns"] if c["name"] == "score")["dtype"] == "numeric"


def test_raw_tables_retained_in_memory_and_wiped_on_close():
    """The raw frames live in the session (in-memory, never a disk file) and are wiped
    on session close — the ephemeral guarantee covers them like the cleaned tables."""
    app, client = _client()
    sid = _new_session(client)
    h = {"X-Session-Id": sid}
    client.post("/upload", files=[("files", ("zoo.csv", _ZOO_CSV, "text/csv"))], headers=h)

    sess = app.state.store._sessions[sid]
    assert sess.db_location == IN_MEMORY                 # never a disk path
    assert "zoo" in sess.raw_tables and len(sess.raw_tables["zoo"]) == 3

    client.delete("/session", headers=h)                # explicit wipe
    assert sess.closed is True
    assert sess.raw_tables == {} and sess.tables == {}  # raw frames gone too


# ── 6e. RE-UPLOAD WARNS WHEN IT WOULD DISCARD MANUAL JOINS ───────────────────────

def test_reupload_warns_only_when_manual_joins_would_be_lost():
    app, client = _client(nl2sql=lambda q: _MANUAL_JOIN_SQL)
    sid = _new_session(client)
    h = {"X-Session-Id": sid}

    # first upload: no contract/manual joins yet → no warning
    up1 = client.post(
        "/upload",
        files=[
            ("files", ("depts.csv", _DEPTS_CSV, "text/csv")),
            ("files", ("staff.csv", _STAFF_CSV, "text/csv")),
        ],
        headers=h,
    )
    assert up1.status_code == 200, up1.text
    assert up1.json()["warnings"] == []

    # define a manual join (lives only on the cached contract)
    cs = client.post(
        "/confirm-schema",
        json={"manual_relationships": [
            {"from_table": "staff", "from_col": "team", "to_table": "depts", "to_col": "k"}
        ]},
        headers=h,
    )
    assert cs.status_code == 200, cs.text

    # re-upload: the rebuild discards the manual join → a clear, non-silent warning
    up2 = client.post(
        "/upload",
        files=[("files", ("zoo.csv", _ZOO_CSV, "text/csv"))],
        headers=h,
    )
    assert up2.status_code == 200, up2.text
    warnings = up2.json()["warnings"]
    assert len(warnings) == 1
    assert "manually-defined join" in warnings[0]
    assert "staff.team" in warnings[0] and "depts.k" in warnings[0]

    # a further re-upload now has no manual joins left to lose → no warning
    up3 = client.post(
        "/upload",
        files=[("files", ("zoo.csv", _ZOO_CSV, "text/csv"))],
        headers=h,
    )
    assert up3.json()["warnings"] == []


# ── 7. UNKNOWN SESSION ────────────────────────────────────────────────────────────

def test_unknown_session_is_404():
    app, client = _client()
    assert client.get("/schema", headers={"X-Session-Id": "does-not-exist"}).status_code == 404
    assert client.post("/upload", files=_upload_files("02_join_pair/customers.csv"),
                       headers={"X-Session-Id": "nope"}).status_code == 404
