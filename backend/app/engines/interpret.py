"""
interpret.py — Tier-2 AI interpretation (only runs when the strict Tier-1 gate
misses).

The deterministic gate (nl2sql.select_relevant) maps a question to columns by
name/synonym overlap. It is intentionally literal, so it misses value-based or
vague phrasing that is nonetheless answerable — e.g. on a trades table
"how many bond1 sold" means SUM(Quantity) WHERE BondID='BOND1' AND BuySell='SELL',
but neither "bond1" nor "sold" is a column NAME.

Tier 2 asks the LLM to PROPOSE A STRUCTURED MAPPING of the question onto real
schema elements — which tables/columns, which value-level filters, the aggregation,
and a confidence signal. It returns a mapping, never SQL and never a number.

The engine then OWNS the answer:
  - it re-validates every table/column/value in the proposal against the real
    schema and data (hallucinated mappings are rejected, never executed),
  - low-confidence / ambiguous proposals are routed to a clarify question (the LLM
    never silently picks),
  - only a confident, fully-valid proposal is turned into SQL via the existing
    generation + guardrail + execution path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.engines import nl2sql as nl2sql_mod
from app.llm.base import LLMProvider
from app.models import MappingAlternative, MappingFilter, MappingProposal, SchemaContract

SYSTEM_TAG = "ROLE: javaab-interpret"

_AGGS = {"SUM", "COUNT", "AVG", "MIN", "MAX"}

_FEWSHOT = """Examples (JSON only — a MAPPING, never SQL, never a number):

Q: how many bond1 sold
Schema: trades(bond_id [values: BOND1, BOND2], buy_sell [values: BUY, SELL], quantity)
{"tables":["trades"],"columns":["quantity"],"filters":[{"column":"bond_id","op":"=","value":"BOND1"},{"column":"buy_sell","op":"=","value":"SELL"}],"aggregation":"SUM","measure":"quantity","group_by":[],"confidence":"high","alternatives":[],"unmappable":false,"reason":"bond1->BondID, sold->BuySell='SELL'"}

Q: how many were sold   (schema has TWO columns whose values include 'SELL': buy_sell AND order_state)
{"tables":["trades"],"columns":[],"filters":[],"aggregation":null,"measure":null,"group_by":[],"confidence":"low","alternatives":[{"term":"sold","options":["buy_sell","order_state"]}],"unmappable":false,"reason":"'sold' could mean buy_sell='SELL' or order_state='SOLD'","proposed_question":"how many records where buy_sell is SELL"}

Q: what's the total profit   (schema has revenue but NO cost/profit column)
{"tables":[],"columns":[],"filters":[],"aggregation":null,"measure":null,"group_by":[],"confidence":"low","alternatives":[],"unmappable":true,"reason":"no cost column exists, profit cannot be derived"}
"""

_SYSTEM = f"""{SYSTEM_TAG}
You map a vague natural-language question onto the REAL schema below. You return a
STRUCTURED MAPPING ONLY — never SQL, never a computed number.

Output STRICT JSON, this exact shape:
{{"tables":[..],"columns":[..],
  "filters":[{{"column":"<real column>","op":"=","value":"<real value>"}}],
  "aggregation":"SUM|COUNT|AVG|MIN|MAX|null","measure":"<column or null>",
  "group_by":[..],"confidence":"high|low",
  "alternatives":[{{"term":"<user phrase>","options":["<col-or-col=val>",..]}}],
  "unmappable":<bool>,"reason":"<short note of what you interpreted>",
  "proposed_question":"<concrete restatement, or null>"}}

Rules:
- Use ONLY tables/columns that appear in the schema, by their exact names. Map a
  user VALUE to a real value: if the user says "bond1" and column bond_id has a
  value 'BOND1', emit a filter bond_id = 'BOND1'. If "sold" and buy_sell has value
  'SELL', emit buy_sell = 'SELL'.
- Prefer columns/values that ACTUALLY appear (names + sample values shown).
- confidence="high" ONLY when there is ONE clear interpretation. If a term could
  plausibly mean two or more different columns/values, set confidence="low" and
  list them in `alternatives` — do NOT pick one.
- When confidence="low" (you're asking the user), ALSO set `proposed_question` to a
  single, concrete, unambiguous restatement of your MOST-LIKELY reading, phrased
  using the real columns/values — so an affirmative can re-run that exact question.
  Use null when there is no sensible single best guess. (Never set it for an
  unmappable concept.)
- If the question needs a concept/column that does NOT exist (e.g. profit with no
  cost column), set unmappable=true. Do NOT invent a column to satisfy it.
- Never output SQL. Never output an answer value.

{_FEWSHOT}"""


@dataclass
class MappingValidation:
    """Result of re-validating a proposal against the real schema + data."""
    ok: bool
    missing: str | None = None          # the first table/column/value that doesn't exist
    note: str = ""                       # human transparency note (what we'll run)
    filters_resolved: list[tuple[str, str, object]] = field(default_factory=list)


def propose_mapping(
    question: str,
    contract: SchemaContract,
    provider: LLMProvider,
    *,
    max_tokens: int = 600,
) -> MappingProposal:
    """Ask the LLM for a structured mapping of the question onto the schema.
    Shows the FULL schema with sample values so value-level mapping is possible."""
    schema_text = nl2sql_mod._schema_text(contract, nl2sql_mod.Relevant.full(contract))
    user = (
        f"Schema:\n{schema_text}\n\n"
        f"Question: {question}\n\n"
        "Return the JSON mapping only."
    )
    raw = provider.complete_json(_SYSTEM, user, max_tokens=max_tokens)

    agg = raw.get("aggregation")
    if isinstance(agg, str):
        agg = agg.strip().upper() or None
        if agg not in _AGGS:
            agg = None

    filters = []
    for f in raw.get("filters") or []:
        if isinstance(f, dict) and f.get("column"):
            filters.append(MappingFilter(
                column=str(f.get("column")),
                op=f.get("op") if f.get("op") in ("=", "!=", ">", "<", ">=", "<=") else "=",
                value=f.get("value"),
            ))

    alts = []
    for a in raw.get("alternatives") or []:
        if isinstance(a, dict):
            alts.append(MappingAlternative(
                term=str(a.get("term", "")),
                options=[str(o) for o in (a.get("options") or [])],
            ))

    conf = raw.get("confidence")
    conf = conf if conf in ("high", "low") else "low"

    return MappingProposal(
        tables=[str(t) for t in (raw.get("tables") or [])],
        columns=[str(c) for c in (raw.get("columns") or [])],
        filters=filters,
        aggregation=agg,
        measure=(str(raw["measure"]) if raw.get("measure") else None),
        group_by=[str(g) for g in (raw.get("group_by") or [])],
        confidence=conf,
        alternatives=alts,
        unmappable=bool(raw.get("unmappable", False)),
        reason=(str(raw["reason"]) if raw.get("reason") else None),
        proposed_question=(str(raw["proposed_question"]) if raw.get("proposed_question") else None),
    )


def _column_table(contract: SchemaContract, column: str, prefer: list[str]) -> str | None:
    """Find a table that actually has `column`, preferring the proposal's tables."""
    col = column.lower()
    ordered = [contract.table(t) for t in prefer if contract.table(t)]
    ordered += [t for t in contract.tables if t not in ordered]
    for tc in ordered:
        if tc and any(c.name.lower() == col for c in tc.columns):
            return tc.name
    return None


def _value_exists(series: pd.Series, value: object) -> bool:
    """Is `value` a real value of this column? Case-insensitive for text so a
    casing mismatch ('sell' vs 'SELL') still resolves; exact for numerics."""
    if value is None:
        return False
    non_null = series.dropna()
    if isinstance(value, str):
        target = value.strip().lower()
        return any(str(v).strip().lower() == target for v in non_null)
    return any(v == value for v in non_null)


def validate_mapping(
    proposal: MappingProposal,
    contract: SchemaContract,
    tables: dict[str, pd.DataFrame],
) -> MappingValidation:
    """Re-validate the AI's proposal against the REAL schema + data BEFORE any SQL
    runs. Every table/column must exist; every equality filter value must exist in
    its column (where checkable). The FIRST thing that doesn't exist fails the whole
    proposal — a hallucinated mapping must never reach execution."""
    if not proposal.tables:
        return MappingValidation(ok=False, missing="<no table>")

    known = {t.name.lower(): t for t in contract.tables}
    for t in proposal.tables:
        if t.lower() not in known:
            return MappingValidation(ok=False, missing=f"table '{t}'")

    # every referenced column (select cols + measure + group_by + filter cols) exists
    referenced = list(proposal.columns) + list(proposal.group_by)
    if proposal.measure:
        referenced.append(proposal.measure)
    referenced += [f.column for f in proposal.filters]
    for col in referenced:
        if _column_table(contract, col, proposal.tables) is None:
            return MappingValidation(ok=False, missing=f"column '{col}'")

    # every equality filter VALUE must actually exist in its column
    resolved: list[tuple[str, str, object]] = []
    for f in proposal.filters:
        host = _column_table(contract, f.column, proposal.tables)
        if f.op in ("=", "!=") and isinstance(f.value, str):
            df = tables.get(host)
            if df is not None and f.column in df.columns:
                if not _value_exists(df[f.column], f.value):
                    return MappingValidation(ok=False, missing=f"value '{f.value}' in column '{f.column}'")
        resolved.append((f.column, f.op, f.value))

    # human-readable transparency note for the answer
    parts = []
    if proposal.aggregation and proposal.measure:
        parts.append(f"{proposal.aggregation}({proposal.measure})")
    if proposal.filters:
        conds = " and ".join(
            f"{f.column} {f.op} {f.value!r}" if isinstance(f.value, str) else f"{f.column} {f.op} {f.value}"
            for f in proposal.filters
        )
        parts.append(f"where {conds}")
    if proposal.group_by:
        parts.append("by " + ", ".join(proposal.group_by))
    note = "Read as: " + " ".join(parts) if parts else ""

    return MappingValidation(ok=True, note=note, filters_resolved=resolved)


def is_ambiguous(proposal: MappingProposal) -> bool:
    """Low confidence, or two+ plausible interpretations the AI listed — either way
    we must ask, not guess."""
    if proposal.confidence == "low":
        return True
    return any(len(a.options) >= 2 for a in proposal.alternatives)


def clarify_text(proposal: MappingProposal) -> str:
    """Build a clarify question naming the options the AI was torn between."""
    spelled = []
    for a in proposal.alternatives:
        if a.options:
            spelled.append(f"'{a.term}' could mean {', '.join(a.options)}")
    if spelled:
        return (
            "Your question could be read more than one way: "
            + "; ".join(spelled)
            + ". Which did you mean?"
        )
    if proposal.reason:
        return f"I'm not fully sure how to read that ({proposal.reason}). Can you clarify?"
    return "I'm not fully sure how to read that — can you clarify which fields you mean?"
