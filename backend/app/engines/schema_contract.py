"""
schema_contract.py — THE CENTERPIECE.

Assembles ONE confidence-scored object from everything the deterministic engines
already know (profiler + joins + cleaning flags) plus the LLM semantic labels
(schema_ai). That object — the SchemaContract — is the single source of truth
handed to SQL generation. Nothing downstream guesses about the schema.

Per column it carries: plain-English meaning, business role, a 0..1 confidence,
and a `provisional` flag when confidence is low OR the cleaning stage flagged the
column (e.g. an ambiguous date order from fixture 04). The relationship graph
carries FK→PK edges with their own confidence. When something is provisional, a
clarifying_question is attached so the system can ASK rather than fabricate.
"""
from __future__ import annotations

import pandas as pd

from app.engines import joins as joins_mod
from app.engines import profiler as profiler_mod
from app.engines import schema_ai
from app.llm.base import LLMProvider
from app.models import (
    ColumnContract,
    RelationshipEdge,
    SchemaContract,
    TableContract,
)

#: a column at/below this confidence is provisional → SQL must hedge / ask.
DEFAULT_CONFIDENCE_THRESHOLD = 0.55


def _heuristic_confidence(profile: dict) -> float:
    """Deterministic confidence when the LLM gives us nothing for a column.
    Clear, well-shaped columns (ids, typed measures/dates) score high; coded or
    high-null text columns score low so they surface for confirmation."""
    role = profile.get("role")
    dtype = profile.get("dtype")
    null_pct = profile.get("null_pct", 0.0)
    name = profile.get("norm_name", "")

    is_id = profile.get("is_id") or role == "id"
    if is_id:
        base = 0.9
    elif role == "timestamp" or dtype == "date":
        base = 0.8
    elif role == "measure":
        base = 0.78
    elif role == "dimension":
        base = 0.7
    else:  # free text
        base = 0.55

    # short coded names (cst, amt, sgmt) are inherently less certain — but an
    # id-shaped key (`id`) is unambiguous regardless of length, so don't penalise it.
    if not is_id and len(name) <= 4 and "_" not in name:
        base -= 0.2
    # lots of nulls erodes confidence
    base -= min(0.2, null_pct * 0.4)
    return round(max(0.05, min(1.0, base)), 3)


def _flag_lookup(flags: list[dict] | None) -> dict[tuple[str, str], str]:
    """Map (table, column) → ambiguity kind from cleaning flags (date_order, etc.)."""
    out: dict[tuple[str, str], str] = {}
    for f in flags or []:
        kind = f.get("kind")
        col = f.get("column")
        table = f.get("table")
        if kind in ("ambiguous_date", "date_order", "coerce_failed", "mixed_type") and col:
            out[(table, col)] = kind
    return out


def _date_clarifier(column: str) -> str:
    return (
        f"Column '{column}' has an ambiguous date order (could be DD/MM or MM/DD). "
        f"Which format should I use?"
    )


def build_contract(
    tables: dict[str, pd.DataFrame],
    provider: LLMProvider,
    *,
    flags: list[dict] | None = None,
    profiles: dict[str, dict] | None = None,
    relationships: list[dict] | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    skip_llm: bool = False,
) -> SchemaContract:
    """Assemble the contract. The LLM labelling call happens at most ONCE here;
    callers cache the returned contract for the session so it is never re-sent
    per question.

    `flags` are the cleaning-stage ambiguity flags (harness/orchestrator format:
    {table, column, kind, provisional}). `skip_llm=True` builds a fully
    deterministic contract (used when no key is configured)."""
    # 1. deterministic profiles + relationships (reuse the proven engines)
    if profiles is None:
        profiles = {name: profiler_mod.profile_table(df, name) for name, df in tables.items()}
    if relationships is None:
        relationships = joins_mod.discover_joins(tables, profiles)

    # 2. one LLM semantic-labelling call for the whole upload
    labels = {} if skip_llm else schema_ai.label_schema(profiles, provider)

    flag_map = _flag_lookup(flags)

    # 3. merge into per-column contracts
    table_contracts: list[TableContract] = []
    for table, cols in profiles.items():
        tbl_labels = labels.get(table, {}) if isinstance(labels, dict) else {}
        col_labels = tbl_labels.get("columns", {}) if isinstance(tbl_labels, dict) else {}

        col_contracts: list[ColumnContract] = []
        for col, p in cols.items():
            llm = col_labels.get(col, {}) if isinstance(col_labels, dict) else {}
            meaning = (llm.get("meaning") or "").strip()
            clar = (llm.get("clarifying_question") or None)

            # confidence: trust the LLM when it answered, else heuristic.
            if "confidence" in llm and isinstance(llm["confidence"], (int, float)):
                confidence = float(llm["confidence"])
            else:
                confidence = _heuristic_confidence(p)
            confidence = round(max(0.0, min(1.0, confidence)), 3)

            provisional = confidence < confidence_threshold

            # a cleaning ambiguity forces provisional + a concrete question
            amb = flag_map.get((table, col))
            if amb:
                provisional = True
                if amb in ("ambiguous_date", "date_order"):
                    clar = clar or _date_clarifier(col)
                else:
                    clar = clar or f"Column '{col}' could not be cleanly typed ({amb}). Please confirm its meaning."

            if provisional and not clar:
                clar = (
                    f"I'm not sure what '{col}' represents"
                    + (f" (guess: {meaning})" if meaning else "")
                    + ". Can you describe it?"
                )

            col_contracts.append(ColumnContract(
                name=col,
                raw_name=p.get("raw_name", col),
                dtype=p.get("dtype", "text"),
                role=p.get("role", "text"),
                meaning=meaning,
                confidence=confidence,
                provisional=provisional,
                clarifying_question=clar,
                is_id=bool(p.get("is_id")),
                is_fk=bool(p.get("is_fk")),
                sample_values=[v for v in p.get("sample_values", [])[:5]],
            ))

        summary = tbl_labels.get("summary", "") if isinstance(tbl_labels, dict) else ""
        table_contracts.append(TableContract(
            name=table,
            summary=summary,
            row_count=int(len(tables[table])),
            columns=col_contracts,
        ))

    # 4. relationship edges — medium/low confidence joins are provisional
    edges: list[RelationshipEdge] = []
    for rel in relationships:
        label = rel.get("confidence_label", "low")
        edges.append(RelationshipEdge(
            from_table=rel["from_table"],
            from_col=rel["from_col"],
            to_table=rel["to_table"],
            to_col=rel["to_col"],
            confidence=float(rel.get("confidence", 0.0)),
            confidence_label=label,
            provisional=(label != "high"),
        ))

    return SchemaContract(tables=table_contracts, relationships=edges)
