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


@dataclass
class Relevant:
    """The subset of the contract relevant to a question (drives lean prompt +
    fail-loud)."""
    tables: set[str] = field(default_factory=set)
    columns: dict[str, set[str]] = field(default_factory=dict)  # table -> {cols}
    matched_anything: bool = False

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

    relevant_tables = table_matched | set(col_matched)
    rel.matched_anything = bool(relevant_tables)

    # include full column sets for matched tables (so "list customers" works),
    # but always keep id/fk columns so joins/grouping can be written.
    for tc in contract.tables:
        if tc.name not in relevant_tables:
            continue
        cols = set(col_matched.get(tc.name, set()))
        if tc.name in table_matched:
            cols |= {c.name for c in tc.columns}
        else:
            cols |= {c.name for c in tc.columns if c.is_id or c.is_fk}
        rel.tables.add(tc.name)
        rel.columns[tc.name] = cols

    # pull in tables connected by a relationship to a matched table (needed for
    # cross-file JOIN questions like "revenue by customer segment").
    for edge in contract.relationships:
        if edge.from_table in rel.tables or edge.to_table in rel.tables:
            for t, c in ((edge.from_table, edge.from_col), (edge.to_table, edge.to_col)):
                rel.tables.add(t)
                rel.columns.setdefault(t, set()).add(c)

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
    # relationships among the selected tables
    rels = [
        e for e in contract.relationships
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
"""

_SYSTEM = f"""{SYSTEM_TAG}
You write DuckDB SQL from a natural-language question. Use ONLY the tables and
columns in the provided schema. Return STRICT JSON only, shape:
{{"sql": "<single SELECT or null>", "tables_used": [..], "assumptions": [..],
  "needs_clarification": <bool>, "clarifying_question": "<question or null>"}}

Hard rules:
- ONE read-only SELECT statement. Never DELETE/UPDATE/DROP/INSERT/etc.
- Reference only columns/tables that appear in the schema below.
- For JOINs, use the listed relationships (FK -> PK) — do not invent keys.
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
) -> NL2SQLResult:
    """Build the lean prompt and ask the model for structured SQL."""
    schema_text = _schema_text(contract, relevant)
    user = (
        f"Schema:\n{schema_text}\n\n"
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
