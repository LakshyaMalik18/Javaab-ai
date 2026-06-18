"""
test_stress_multihop_live.py — LIVE multi-hop JOIN stress baseline.

    pytest -m live -s tests/test_stress_multihop_live.py
    # or run standalone for the full report:
    python tests/test_stress_multihop_live.py

Generates linked-table CHAINS of increasing length (2..8 tables) in two variants:
  (a) clean   — natural join-key names (region.country_id -> country.country_id)
  (b) coded   — opaque join-keys (f3 -> k2); table/dimension/measure names stay
                natural so only the KEYS are gibberish (the value-based-join case).

Every chain carries a single grouped measure with a HAND-COMPUTED known answer:
  question = "What is the total amount for each category?"
  truth    = {North: 410, South: 810, East: 1210}   (independent of chain length)

For each case it builds the contract with the REAL pipeline (profiler + value-based
join discovery + real LLM labelling), asks the REAL model for SQL, then checks:
  1. JOIN COMPLETENESS — did the emitted SQL join every table the chain requires?
  2. GUARD FALSE-POSITIVE — did the completeness/cartesian guard refuse a query that
     was actually correct (joined all, no real cartesian)?
  3. NUMERIC TRUTH — does the executed answer equal the known-correct numbers?

Output: a chain-length × key-type grid of PASS / WRONG / REFUSED / GUARD_FP / ERROR,
plus per-case SQL for anything that isn't a clean PASS. The engine is NOT modified.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# make the backend package importable when run standalone (pytest uses conftest)
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import pandas as pd
import pytest
import sqlglot
from sqlglot import exp

from app.engines import schema_contract as contract_mod
from app.engines.orchestrator import answer
from app.engines import nl2sql as nl2sql_mod
from app.llm import get_provider

# Auto-load backend/.env (live tier only), same pattern as test_phase3_live.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

pytestmark = pytest.mark.live

# ── chain generator ─────────────────────────────────────────────────────────────

NAMES = ["country", "region", "district", "store", "aisle", "shelf", "bin", "unit"]
H, K = 3, 4                                   # 3 groups, 4 measure rows each
CATS = ["North", "South", "East"]
# group j sum = sum_{k=1..K} ((j+1)*100 + k) = K*(j+1)*100 + K(K+1)/2
TRUTH = {CATS[j]: K * (j + 1) * 100 + K * (K + 1) // 2 for j in range(H)}  # 410/810/1210
QUESTION = "What is the total amount for each category?"


def make_chain(n: int, coded: bool) -> dict[str, pd.DataFrame]:
    """Linear chain of `n` tables. Key VALUE ranges are disjoint per table so
    value-based discovery links ONLY adjacent tables (a true chain, not a clique).
    `coded` swaps natural key names for opaque ones; everything else is identical."""
    def pk(i: int) -> str:
        return f"k{i}" if coded else f"{NAMES[i]}_id"

    def fk(i: int) -> str:
        return f"f{i}" if coded else f"{NAMES[i - 1]}_id"

    tables: dict[str, pd.DataFrame] = {}
    # head: groups + the dimension the question groups by
    tables[NAMES[0]] = pd.DataFrame({pk(0): list(range(H)), "category": CATS})
    # middle bridges: 1:1 link rows carrying only keys
    for i in range(1, n - 1):
        tables[NAMES[i]] = pd.DataFrame({
            pk(i): [i * 1000 + j for j in range(H)],
            fk(i): [(i - 1) * 1000 + j for j in range(H)],
        })
    # tail: the measure (K rows per group)
    i = n - 1
    rows = H * K
    tables[NAMES[i]] = pd.DataFrame({
        pk(i): [i * 1000 + r for r in range(rows)],
        fk(i): [(i - 1) * 1000 + (r // K) for r in range(rows)],
        "amount": [(r // K + 1) * 100 + (r % K + 1) for r in range(rows)],
    })
    return tables


# ── per-case evaluation ─────────────────────────────────────────────────────────

def _sql_tables(sql: str | None) -> set[str]:
    if not sql:
        return set()
    try:
        stmt = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return set()
    return {t.name.lower() for t in stmt.find_all(exp.Table)}


def _real_cartesian(sql: str | None) -> bool:
    """True if the SQL has a base-table join with no ON/USING (genuine cartesian)."""
    if not sql:
        return False
    try:
        stmt = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return False
    for j in stmt.find_all(exp.Join):
        if isinstance(j.this, exp.Table) and not j.args.get("on") and not j.args.get("using"):
            return True
    return False


def _parse_rows(rows: list[dict]) -> dict:
    if not rows:
        return {}
    key_cat = next((k for k in rows[0] if "cat" in k.lower()), None) or list(rows[0])[0]
    key_amt = next((k for k in rows[0] if k != key_cat), None)
    out: dict = {}
    for r in rows:
        try:
            out[str(r[key_cat])] = round(float(r[key_amt]), 2)
        except (TypeError, ValueError, KeyError):
            return {}
    return out


def _bad_columns(sql: str | None, contract) -> bool:
    """True if the SQL references a column not present in the schema (model error)."""
    if not sql:
        return False
    try:
        stmt = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return True
    schema = {t.name.lower(): {c.name.lower() for c in t.columns} for t in contract.tables}
    alias: dict[str, str] = {}
    for t in stmt.find_all(exp.Table):
        if t.name.lower() in schema:
            alias[(t.alias_or_name or t.name).lower()] = t.name.lower()
    allcols = set().union(*schema.values()) if schema else set()
    for col in stmt.find_all(exp.Column):
        cn = col.name.lower()
        if col.table:
            tgt = alias.get(col.table.lower())
            if tgt and cn not in schema[tgt]:
                return True
        elif allcols and cn not in allcols and cn != "*":
            return True
    return False


def run_case(n: int, coded: bool, provider) -> dict:
    tables = make_chain(n, coded)
    required = set(tables)                     # all n tables must be joined

    # Deterministic schema (profiler + value-based join discovery); skip the LLM
    # LABELLING call so the only REAL-model step is the SQL composition we're
    # actually stress-testing (and to conserve free-tier quota across 14 cases).
    contract = contract_mod.build_contract(tables, provider, skip_llm=True)

    # diagnostic: did selection itself resolve the whole chain (pre-LLM)?
    rel = nl2sql_mod.select_relevant(QUESTION, contract)
    path_len = len(rel.required_tables)

    res = answer(QUESTION, tables, contract=contract, provider=provider)

    sql = res.sql
    joined = _sql_tables(sql)
    joined_all = required <= joined

    note = ""
    if res.status == "answered":
        got = _parse_rows(res.rows)
        correct = got == {k: float(v) for k, v in TRUTH.items()}
        if not joined_all:
            outcome = "UNDERJOIN"             # answered without all tables (guard gap)
        elif correct:
            outcome = "PASS"
        else:
            outcome = "WRONG"                 # joined all but wrong number / 0 rows
        note = "" if outcome == "PASS" else f"rows={len(res.rows)} got={got}"
    elif res.status in ("refused", "blocked"):
        detail = (res.clarifying_question or res.blocked_reason or "")
        low = detail.lower()
        # classify WHY it was refused so a legitimate catch isn't miscounted as a
        # guard false-positive:
        if "left out" in low or "partial join" in low:
            outcome = "REFUSED_PARTIAL"       # completeness guard caught a dropped table
        elif "cartesian" in low or "cross join" in low:
            outcome = "REFUSED_CARTESIAN"     # cartesian guard caught a cross join
        elif "unknown column" in low or "isn't in your data" in low or "field that isn't" in low:
            outcome = "BADCOL"                # model put a column on the wrong table (its error)
        elif joined_all and not _real_cartesian(sql) and not _bad_columns(sql, contract):
            outcome = "GUARD_FP"              # genuine false positive: correct SQL refused
        else:
            outcome = "REFUSED"
        note = detail.strip().replace("\n", " ")[:90]
    else:
        outcome = "RLIMIT" if res.error_kind == "rate_limit" else "ERROR"
        note = (res.error or "").replace("\n", " ")[:90]

    return {
        "n": n, "coded": coded, "outcome": outcome, "status": res.status,
        "path_len": path_len, "joined": sorted(joined), "joined_all": joined_all,
        "sql": sql, "note": note,
    }


# ── report ───────────────────────────────────────────────────────────────────────

CHAIN_LENGTHS = [2, 3, 4, 5, 6, 7, 8]


def _provider():
    choice = os.environ.get("STRESS_PROVIDER", "").lower()  # "gemini" | "groq" | ""
    if choice == "groq" and os.environ.get("GROQ_API_KEY"):
        return get_provider(privacy_mode=True)         # Groq
    if choice == "gemini" and os.environ.get("GEMINI_API_KEY"):
        return get_provider()
    if os.environ.get("GEMINI_API_KEY"):
        return get_provider()                          # Gemini default
    if os.environ.get("GROQ_API_KEY"):
        return get_provider(privacy_mode=True)         # Groq
    return None


def build_report() -> tuple[str, list[dict]]:
    provider = _provider()
    if provider is None:
        return "SKIPPED — no GEMINI_API_KEY or GROQ_API_KEY in env/.env", []

    import time

    pace = float(os.environ.get("STRESS_PACE_SECONDS", "3"))
    only_n = {int(x) for x in os.environ.get("STRESS_LENGTHS", "").split(",") if x.strip()}
    only_v = os.environ.get("STRESS_VARIANTS", "")  # "clean" | "coded" | ""
    variants = (False,) if only_v == "clean" else (True,) if only_v == "coded" else (False, True)
    results: list[dict] = []
    for n in CHAIN_LENGTHS:
        if only_n and n not in only_n:
            continue
        for coded in variants:
            try:
                results.append(run_case(n, coded, provider))
            except Exception as e:  # keep the grid filling even if one case explodes
                results.append({"n": n, "coded": coded, "outcome": "ERROR",
                                "status": "exception", "path_len": -1, "joined": [],
                                "joined_all": False, "sql": None, "note": str(e)[:90]})
            time.sleep(pace)        # space calls to stay under free-tier RPM

    by = {(r["n"], r["coded"]): r for r in results}
    lines = []
    lines.append(f"\nProvider: {getattr(provider, 'name', '?')} / {getattr(provider, 'model', '?')}")
    lines.append(f"Truth (all lengths): {TRUTH}\n")
    lines.append(f"{'chain':>5} | {'CLEAN keys':<22} | {'CODED keys':<22}")
    lines.append(f"{'-'*5}-+-{'-'*22}-+-{'-'*22}")
    def cell(r):
        if r is None:
            return "-"
        tag = r["outcome"]
        extra = "" if r["joined_all"] else f" sel{r['path_len']}/{r['n']}"
        return f"{tag}{extra}"
    for n in CHAIN_LENGTHS:
        if (n, False) not in by and (n, True) not in by:
            continue
        lines.append(f"{n:>5} | {cell(by.get((n, False))):<22} | {cell(by.get((n, True))):<22}")
    lines.append("\nLegend: PASS=joined all + correct number · WRONG=joined all but wrong #")
    lines.append("        UNDERJOIN=answered w/o all tables · REFUSED=guard caught a bad join")
    lines.append("        GUARD_FP=guard refused a correct query · ERROR=exception/exec fail")
    lines.append(f"        selX/N = select_relevant resolved only X of N tables (pre-LLM)\n")

    # details for anything that isn't a clean PASS
    bad = [r for r in results if r["outcome"] != "PASS"]
    if bad:
        lines.append("Details (non-PASS cases):")
        for r in bad:
            kt = "coded" if r["coded"] else "clean"
            lines.append(f"  [n={r['n']} {kt}] {r['outcome']} ({r['status']})  "
                         f"joined={r['joined']}  note={r['note']}")
            if r["sql"]:
                lines.append(f"      SQL: {r['sql']}")
    return "\n".join(lines), results


def test_stress_multihop_grid():
    report, results = build_report()
    print(report)
    if not results:
        pytest.skip("no live key configured")
    # soft floor: the 2-table case (both variants) must work, else the harness/setup
    # is broken rather than the model hitting its depth limit.
    base = [r for r in results if r["n"] == 2]
    assert all(r["outcome"] == "PASS" for r in base), (
        "2-table baseline failed — setup issue, see printed report")


if __name__ == "__main__":
    report, _ = build_report()
    print(report)
