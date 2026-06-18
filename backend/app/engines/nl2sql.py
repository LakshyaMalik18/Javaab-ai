"""
nl2sql.py — §6.1 natural-language → SQL.

Assembles a LEAN prompt from the SchemaContract: only the tables/columns relevant
to *this* question (plus the relationships that connect them), never the whole
schema every time — that conserves the free-tier token budget. Few-shot examples
cover both a single-table aggregate and a cross-file JOIN.

Structured JSON out:
    { sql, tables_used[], assumptions[], needs_clarification, clarifying_question }

The model is instructed to set needs_clarification=true (and NOT emit SQL) when
the question references something not in the provided schema or is ambiguous.
That is half of the fail-loud behaviour; the orchestrator enforces the other
half deterministically before this is ever called.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

from app.llm.base import LLMError, LLMProvider
from app.models import NL2SQLResult, SchemaContract

SYSTEM_TAG = "ROLE: javaab-nl2sql"

_STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "with", "from", "that", "this",
    "what", "which", "how", "many", "much", "show", "list", "give", "get", "all",
    "per", "each", "have", "has", "had", "did", "does", "into", "over", "than",
    "between", "average", "avg", "total", "sum", "count", "number", "top",
    "most", "least", "last", "first", "group", "order", "where", "when", "who",
    "their", "there", "them", "they", "our", "your", "find", "tell", "about",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 3 and t not in _STOPWORDS}


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


# Business-term synonyms that may map to a single clearly-matching MONETARY measure
# column ("revenue"/"sales" → the amount column). Deliberately NARROW: only terms
# that denote money taken in. Derived metrics (profit, margin, …) are intentionally
# excluded — they are not a raw column and must still fail loud.
_MONETARY_SYNONYMS = {
    "revenue", "revenues", "sales", "turnover", "earnings", "income",
    "takings", "proceeds", "billings", "receipts", "grossings",
}

# Tokens that mark a numeric measure column as monetary, so a money synonym maps to
# an amount/price column and never to a quantity/temperature measure.
_MONETARY_HINTS = {
    "amount", "amt", "price", "cost", "revenue", "sales", "value", "total",
    "fee", "charge", "charges", "payment", "payments", "paid", "spend", "spent",
    "balance", "income", "earnings", "turnover", "subtotal", "gross", "net",
    "usd", "eur", "gbp", "dollars", "money",
}


def _monetary_measure_columns(contract: SchemaContract) -> list[tuple[str, str]]:
    """Numeric measure columns that read as money (name/raw_name/meaning carries a
    monetary hint). These are the candidates a money synonym can resolve to."""
    out: list[tuple[str, str]] = []
    for tc in contract.tables:
        for cc in tc.columns:
            if cc.role != "measure" or cc.dtype != "numeric":
                continue
            text = f"{cc.name} {cc.raw_name} {cc.meaning or ''}".lower()
            if set(_TOKEN_RE.findall(text)) & _MONETARY_HINTS:
                out.append((tc.name, cc.name))
    return out


@dataclass
class Relevant:
    """The subset of the contract relevant to a question (drives lean prompt +
    fail-loud)."""
    tables: set[str] = field(default_factory=set)
    columns: dict[str, set[str]] = field(default_factory=dict)  # table -> {cols}
    matched_anything: bool = False
    # The resolved JOIN-PATH: the anchor tables the question names PLUS every bridge
    # table the relationship graph says is needed to connect them. The generated SQL
    # must reference all of these — a query that omits one is answering from a
    # partial join and is refused (see orchestrator completeness check). Empty for
    # the Tier-2 `.full()` fallback, which owns its own validation.
    required_tables: set[str] = field(default_factory=set)
    # Set when a business synonym (e.g. "revenue") could plausibly map to two or
    # more monetary columns — the deterministic fail-loud for genuine ambiguity.
    clarify_question: str | None = None

    def provisional_hits(self, contract: SchemaContract) -> list[tuple[str, str]]:
        hits: list[tuple[str, str]] = []
        for t, cols in self.columns.items():
            tc = contract.table(t)
            if not tc:
                continue
            for c in cols:
                cc = tc.column(c)
                if cc and cc.provisional:
                    hits.append((t, c))
        return hits

    @classmethod
    def full(cls, contract: SchemaContract) -> "Relevant":
        """Everything in the schema — the interpretation fallback. Used when literal
        token-matching found nothing, so the LLM can still map vague terms/values to
        real columns. matched_anything stays False so the caller knows this is the
        interpret-or-refuse path (the LLM, guardrail and real SQL are the net)."""
        rel = cls(matched_anything=False)
        for tc in contract.tables:
            rel.tables.add(tc.name)
            rel.columns[tc.name] = {c.name for c in tc.columns}
        return rel


def suggest_questions(contract: SchemaContract, limit: int = 4) -> list[str]:
    """Build a few example questions from the ACTUAL schema (real table + column
    names + roles). Returned alongside a refusal so an unmappable question becomes
    a set of clickable starting points instead of a dead end. Deterministic — no
    LLM call. Always returns at least one (every table can be counted)."""
    out: list[str] = []
    for tc in contract.tables:
        measures = [c for c in tc.columns if c.role == "measure"]
        dims = [c for c in tc.columns if c.role == "dimension"]
        times = [c for c in tc.columns if c.role == "timestamp"]
        if measures and dims:
            out.append(f"What is the total {measures[0].name} by {dims[0].name} in {tc.name}?")
        if measures and times:
            out.append(f"How does {measures[0].name} trend over {times[0].name} in {tc.name}?")
        if measures:
            out.append(f"What is the total {measures[0].name} in {tc.name}?")
        out.append(f"How many rows are in {tc.name}?")
        if len(out) >= limit:
            break
    seen: set[str] = set()
    uniq: list[str] = []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:limit]


def _join_path_tables(anchors: set[str], contract: SchemaContract) -> set[str]:
    """Walk the relationship graph and return the smallest table set that CONNECTS
    every anchor — anchors plus the bridge tables on the paths between them.

    This is real multi-hop traversal, replacing the old single-pass one-hop loop
    that silently dropped any bridge sitting two-plus hops from an anchor (and broke
    entirely on coded keys, where bridge names don't leak the parent token). The
    join keys still come only from the known relationships, never guessed.

    Greedy Steiner approximation: grow a connected component, attaching each
    remaining anchor by the shortest path (BFS) to what's already included. Anchors
    with no path to the rest are still kept (the orchestrator's completeness check
    then refuses rather than silently answering from a disconnected fragment)."""
    adj: dict[str, set[str]] = defaultdict(set)
    for e in contract.active_relationships():  # only the active link per pair
        adj[e.from_table].add(e.to_table)
        adj[e.to_table].add(e.from_table)

    ordered = sorted(anchors)
    if not ordered:
        return set()

    included: set[str] = {ordered[0]}
    for target in ordered[1:]:
        if target in included:
            continue
        path = _shortest_path(target, included, adj)
        included.update(path or {target})
    return included


def _shortest_path(start: str, goals: set[str], adj: dict[str, set[str]]) -> set[str] | None:
    """BFS from `start` to the nearest node in `goals`; return every table on that
    path (inclusive), or None if `goals` is unreachable from `start`."""
    if start in goals:
        return {start}
    prev: dict[str, str] = {start: start}
    q: deque[str] = deque([start])
    while q:
        node = q.popleft()
        for nxt in sorted(adj.get(node, ())):
            if nxt in prev:
                continue
            prev[nxt] = node
            if nxt in goals:
                path = {nxt}
                cur = nxt
                while cur != start:
                    cur = prev[cur]
                    path.add(cur)
                return path
            q.append(nxt)
    return None


def select_relevant(question: str, contract: SchemaContract) -> Relevant:
    """Pick the tables/columns this question is about. Generous for the prompt
    (so valid questions aren't starved of context) but only `matched_anything`
    when *something* genuinely maps — an all-miss is the unmapped fail-loud case."""
    q = _tokens(question)
    rel = Relevant()

    table_matched: set[str] = set()
    for tc in contract.tables:
        tname = tc.name.lower()
        if tname in q or _singular(tname) in q or any(_singular(tok) == _singular(tname) for tok in q):
            table_matched.add(tc.name)

    col_matched: dict[str, set[str]] = {}
    for tc in contract.tables:
        for cc in tc.columns:
            name_tokens = set(_TOKEN_RE.findall(cc.name.lower())) | set(_TOKEN_RE.findall(cc.raw_name.lower()))
            meaning_tokens = _tokens(cc.meaning) if cc.meaning else set()
            if (name_tokens & q) or (meaning_tokens & q):
                col_matched.setdefault(tc.name, set()).add(cc.name)

    # Business-synonym mapping: a money word like "revenue"/"sales" maps to the
    # monetary amount column even though the literal word isn't a column name.
    # Single clear candidate → auto-map; two or more → fail loud (clarify).
    if q & _MONETARY_SYNONYMS:
        candidates = _monetary_measure_columns(contract)
        # don't re-trigger on a column the question already named directly
        already = {(t, c) for t, cs in col_matched.items() for c in cs}
        if not already & set(candidates):  # the money concept isn't pinned yet
            if len(candidates) == 1:
                t, c = candidates[0]
                col_matched.setdefault(t, set()).add(c)
            elif len(candidates) >= 2:
                opts = ", ".join(f"{t}.{c}" for t, c in candidates)
                rel.clarify_question = (
                    f"Your question could mean more than one money column ({opts}). "
                    "Which one should I use?"
                )
                rel.matched_anything = True

    anchors = table_matched | set(col_matched)
    rel.matched_anything = rel.matched_anything or bool(anchors)

    # Resolve the full JOIN-PATH: the anchor tables PLUS every bridge table the
    # relationship graph requires to connect them (real multi-hop traversal, not the
    # old accidental one-hop). This is the set the SQL must join in full.
    path_tables = _join_path_tables(anchors, contract)
    rel.required_tables = set(path_tables)

    # Include the FULL column set of every table on the path — anchors AND bridges.
    # The model needs sibling columns (and sample values) to interpret value/synonym
    # filters ("how many bond1 sold" must reach BondID/BuySell), and a bridge pulled
    # in by traversal must contribute the columns the query actually needs — not just
    # its join keys (the bug the audit flagged). The guardrail is still the net: it
    # rejects any column that isn't really in the table.
    for tc in contract.tables:
        if tc.name not in path_tables:
            continue
        rel.tables.add(tc.name)
        rel.columns[tc.name] = {c.name for c in tc.columns}

    return rel


def _schema_text(contract: SchemaContract, relevant: Relevant) -> str:
    lines: list[str] = []
    for tc in contract.tables:
        if tc.name not in relevant.tables:
            continue
        cols = relevant.columns.get(tc.name) or {c.name for c in tc.columns}
        if tc.summary:
            lines.append(f"Table {tc.name} — {tc.summary} ({tc.row_count} rows)")
        else:
            lines.append(f"Table {tc.name} ({tc.row_count} rows)")
        for cc in tc.columns:
            if cc.name not in cols:
                continue
            tag = []
            if cc.is_id:
                tag.append("PK?")
            if cc.is_fk:
                tag.append("FK")
            if cc.provisional:
                tag.append("PROVISIONAL")
            tagstr = f" [{','.join(tag)}]" if tag else ""
            meaning = f" — {cc.meaning}" if cc.meaning else ""
            lines.append(f"  {cc.name} ({cc.dtype}, {cc.role}){tagstr}{meaning}")
            # Show sample values for low-cardinality / identifying columns so the
            # model can map a user's value or synonym to a real value that EXISTS
            # (e.g. "bond1" → BondID = 'BOND1', "sold" → BuySell = 'SELL').
            if cc.role in ("dimension", "id", "text", "boolean") and cc.sample_values:
                vals = ", ".join(str(v) for v in cc.sample_values[:6])
                lines.append(f"    e.g. values: {vals}")
    # relationships among the selected tables — only the active link per pair, so
    # the model is offered exactly the join the user confirmed (never an alternative)
    rels = [
        e for e in contract.active_relationships()
        if e.from_table in relevant.tables and e.to_table in relevant.tables
    ]
    if rels:
        lines.append("Relationships (FK -> PK):")
        for e in rels:
            lines.append(
                f"  {e.from_table}.{e.from_col} -> {e.to_table}.{e.to_col} "
                f"(confidence {e.confidence:.2f})"
            )
    return "\n".join(lines)


_FEWSHOT = """Examples (DuckDB dialect, JSON out):

Q: How many orders are there?
{"sql":"SELECT COUNT(*) AS order_count FROM orders","tables_used":["orders"],"assumptions":[],"needs_clarification":false,"clarifying_question":null}

Q: Total amount by customer segment
{"sql":"SELECT c.segment, SUM(o.amount) AS total_amount FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.segment ORDER BY total_amount DESC","tables_used":["orders","customers"],"assumptions":["'total' means SUM of amount"],"needs_clarification":false,"clarifying_question":null}

Q: What is the total revenue?   (schema has one money column: orders.amount)
{"sql":"SELECT SUM(amount) AS total_revenue FROM orders","tables_used":["orders"],"assumptions":["mapped 'revenue' to the amount column"],"needs_clarification":false,"clarifying_question":null}

Q: What is the total revenue?   (schema has TWO money columns: orders.amount and orders.refund_amount)
{"sql":null,"tables_used":[],"assumptions":[],"needs_clarification":true,"clarifying_question":"Do you mean amount or refund_amount?"}

Q: how many bond1 sold   (schema: trades(bond_id [values: BOND1, BOND2], buy_sell [values: BUY, SELL], quantity))
{"sql":"SELECT SUM(quantity) AS total_sold FROM trades WHERE bond_id = 'BOND1' AND buy_sell = 'SELL'","tables_used":["trades"],"assumptions":["mapped 'bond1' to bond_id = 'BOND1'","mapped 'sold' to buy_sell = 'SELL'","'how many' = SUM(quantity)"],"needs_clarification":false,"clarifying_question":null}

Q: what's the total profit   (schema has revenue but NO cost/profit column anywhere)
{"sql":null,"tables_used":[],"assumptions":[],"needs_clarification":true,"clarifying_question":"I can see revenue but there's no cost column, so I can't compute profit. Want total revenue instead?"}
"""

_SYSTEM = f"""{SYSTEM_TAG}
You write DuckDB SQL from a natural-language question. Use ONLY the tables and
columns in the provided schema. Return STRICT JSON only, shape:
{{"sql": "<single SELECT or null>", "tables_used": [..], "assumptions": [..],
  "needs_clarification": <bool>, "clarifying_question": "<question or null>"}}

Hard rules:
- ONE read-only SELECT statement. Never DELETE/UPDATE/DROP/INSERT/etc.
- Reference only columns/tables that appear in the schema below.
- Use the EXACT table and column names from the schema, verbatim. The names in the
  examples below (orders, customers, amount, ...) are ILLUSTRATIVE ONLY — never
  emit a table/column name that is not in the provided schema, even if an example
  uses it. If the schema's only table is "events", every FROM/JOIN must say events.
- For JOINs, use the listed relationships (FK -> PK) — do not invent keys.
- INTERPRET vague user language. You MAY map synonyms, informal terms and concrete
  VALUES to the real columns/values shown in the schema:
    * synonyms → the matching column: "revenue"/"sales"/"turnover" → the amount column.
    * a user-typed value → a real row value: "bond1" → a column whose sample values
      include 'BOND1' (write WHERE that_column = 'BOND1').
    * an informal state → the column/value that encodes it: "sold" → buy_sell = 'SELL'
      when a column's sample values include 'SELL'.
  Prefer a value/column that ACTUALLY appears in the schema (names + sample values).
  This is encouraged interpretation, not a guess.
- TRANSPARENCY: record EVERY interpretation you make in `assumptions`, one per
  mapping (e.g. "mapped 'bond1' to bond_id = 'BOND1'"). The user is shown these.
- BUT if a term could plausibly mean two or more different columns (e.g. both an
  `amount` and a `refund_amount` column, or amount in two tables), do NOT pick one:
  set needs_clarification=true, sql=null, and ask which field.
- If the question asks for a column/table/concept NOT in the schema, or is
  genuinely ambiguous, set needs_clarification=true, sql=null, and write a
  specific clarifying_question. Do NOT fabricate columns or guess.
- State any interpretation (e.g. "last month") in `assumptions`.

{_FEWSHOT}"""


def generate_sql(
    question: str,
    contract: SchemaContract,
    relevant: Relevant,
    provider: LLMProvider,
    *,
    max_tokens: int = 700,
    mapping_hint: str | None = None,
) -> NL2SQLResult:
    """Build the lean prompt and ask the model for structured SQL.

    `mapping_hint` is a Tier-2 confirmed interpretation (already re-validated
    against the schema). When present it anchors generation so the SQL reflects the
    interpretation the engine vetted — the guardrail still validates the result."""
    schema_text = _schema_text(contract, relevant)
    hint = f"\nConfirmed interpretation (write SQL for exactly this): {mapping_hint}\n" if mapping_hint else ""
    user = (
        f"Schema:\n{schema_text}\n{hint}\n"
        f"Question: {question}\n\n"
        "Return JSON only."
    )
    try:
        raw = provider.complete_json(_SYSTEM, user, max_tokens=max_tokens)
    except LLMError:
        raise

    sql = raw.get("sql")
    if isinstance(sql, str) and not sql.strip():
        sql = None
    return NL2SQLResult(
        sql=sql,
        tables_used=list(raw.get("tables_used") or []),
        assumptions=list(raw.get("assumptions") or []),
        needs_clarification=bool(raw.get("needs_clarification", False)),
        clarifying_question=raw.get("clarifying_question"),
    )
