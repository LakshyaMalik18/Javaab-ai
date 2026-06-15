from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


class ChangeRecord(BaseModel):
    table: str
    column: str
    rule: str
    cells_affected: int
    before_sample: list[Any] = Field(default_factory=list)
    after_sample: list[Any] = Field(default_factory=list)
    reversible: bool = True


class ChangeLedger(BaseModel):
    records: list[ChangeRecord] = Field(default_factory=list)
    total_cells_affected: int = 0

    def add(self, record: ChangeRecord) -> None:
        self.records.append(record)
        self.total_cells_affected += record.cells_affected


class DuplicateGroup(BaseModel):
    row_indices: list[int]
    sample: dict[str, Any]


class AmbiguityFlag(BaseModel):
    column: str
    kind: Literal["date_order", "mixed_type", "coerce_failed"]
    detail: str


class IngestResult(BaseModel):
    table_name: str
    df_json: str  # DataFrame serialised as JSON orient="split"
    ledger: ChangeLedger
    duplicate_groups: list[DuplicateGroup] = Field(default_factory=list)
    ambiguity_flags: list[AmbiguityFlag] = Field(default_factory=list)
    row_count: int = 0
    col_count: int = 0


class ColumnProfile(BaseModel):
    raw_name: str
    norm_name: str
    dtype: str
    null_pct: float
    distinct_count: int
    cardinality_ratio: float
    sample_values: list[Any] = Field(default_factory=list)
    numeric_min: float | None = None
    numeric_max: float | None = None
    date_min: str | None = None
    date_max: str | None = None
    avg_len: float | None = None
    pattern_fingerprint: list[str] = Field(default_factory=list)
    likely_role: Literal["id", "dimension", "measure", "timestamp", "text"] = "text"


class JoinEdge(BaseModel):
    left_table: str
    left_col: str
    right_table: str
    right_col: str
    score: float
    confidence: Literal["high", "medium", "low"]
    name_sim: float
    value_containment: float
    cardinality_ok: bool


# ── Phase 3: the confidence-scored schema contract ────────────────────────────

class ColumnContract(BaseModel):
    """One column in the schema contract — the single source of truth for SQL gen."""
    name: str                                   # normalised column name (used in SQL)
    raw_name: str
    dtype: str                                  # numeric | date | boolean | text
    role: str                                   # id | dimension | measure | timestamp | text
    meaning: str = ""                           # plain-English meaning (LLM-supplied)
    confidence: float = 0.5                     # 0..1 — how sure we are of `meaning`/role
    provisional: bool = False                   # low-confidence or ambiguous → SQL must hedge
    clarifying_question: str | None = None      # asked instead of guessing, when provisional
    is_id: bool = False
    is_fk: bool = False
    sample_values: list[Any] = Field(default_factory=list)


class TableContract(BaseModel):
    name: str
    summary: str = ""
    row_count: int = 0
    columns: list[ColumnContract] = Field(default_factory=list)

    def column(self, name: str) -> ColumnContract | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None


class RelationshipEdge(BaseModel):
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    confidence: float
    confidence_label: Literal["high", "medium", "low"]
    provisional: bool = False


class SchemaContract(BaseModel):
    """Assembled from profiler + joins + cleaning flags + LLM labels.
    Handed verbatim to nl2sql; nothing downstream guesses about the schema."""
    tables: list[TableContract] = Field(default_factory=list)
    relationships: list[RelationshipEdge] = Field(default_factory=list)

    def table(self, name: str) -> TableContract | None:
        for t in self.tables:
            if t.name == name:
                return t
        return None

    def guardrail_schema(self) -> dict[str, set[str]]:
        """{table: {columns}} for guardrail.validate_sql."""
        return {t.name: {c.name for c in t.columns} for t in self.tables}

    def provisional_columns(self) -> list[tuple[str, str]]:
        return [
            (t.name, c.name)
            for t in self.tables
            for c in t.columns
            if c.provisional
        ]


class NL2SQLResult(BaseModel):
    sql: str | None = None
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarifying_question: str | None = None


class InsightResult(BaseModel):
    insight: str = ""
    followups: list[str] = Field(default_factory=list)


class AnswerResult(BaseModel):
    """Final end-to-end result of orchestrator.answer()."""
    status: Literal["answered", "clarify", "refused", "blocked", "error"]
    question: str
    insight: str | None = None
    sql: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    blocked_reason: str | None = None
    error: str | None = None
    error_kind: str | None = None  # e.g. "rate_limit" — lets the web layer pick an HTTP code
    chart_hint: str | None = None  # deterministic UI hint (single_value | bar | line | table)
    provider_used: str | None = None  # set only when a rate-limit fallback was used
    fallback_note: str | None = None  # small honest note shown when fallback kicked in
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    tables_used: list[str] = Field(default_factory=list)
