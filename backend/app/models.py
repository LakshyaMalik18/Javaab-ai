from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


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
    #: exactly ONE edge per connected table-pair is active; only active edges are
    #: load-bearing at query time (nl2sql prompt + join-path + guardrail whitelist).
    active: bool = False

    def pair_key(self) -> frozenset:
        """Undirected table-pair this edge connects (selection is per pair)."""
        return frozenset({self.from_table, self.to_table})


class SchemaContract(BaseModel):
    """Assembled from profiler + joins + cleaning flags + LLM labels.
    Handed verbatim to nl2sql; nothing downstream guesses about the schema."""
    tables: list[TableContract] = Field(default_factory=list)
    relationships: list[RelationshipEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _seed_active_links(self) -> "SchemaContract":
        # If there are edges but none is active (fresh construction), apply the
        # per-pair default so query time always has exactly one link per pair. A
        # contract that already carries an active selection is left untouched.
        if self.relationships and not any(e.active for e in self.relationships):
            self.default_activate_relationships()
        return self

    def table(self, name: str) -> TableContract | None:
        for t in self.tables:
            if t.name == name:
                return t
        return None

    def guardrail_schema(self) -> dict[str, set[str]]:
        """{table: {columns}} for guardrail.validate_sql."""
        return {t.name: {c.name for c in t.columns} for t in self.tables}

    def active_relationships(self) -> list["RelationshipEdge"]:
        """The single load-bearing edge per connected table-pair. This is what the
        nl2sql prompt, the join-path walk, and the guardrail whitelist all see — an
        inactive (alternative) edge is invisible to query time."""
        return [e for e in self.relationships if e.active]

    def default_activate_relationships(self) -> None:
        """Activate exactly one edge per table-pair: the highest-confidence one.
        Idempotent; the source of the per-pair defaults the UI starts from."""
        best: dict[frozenset, RelationshipEdge] = {}
        for e in self.relationships:
            e.active = False
            cur = best.get(e.pair_key())
            if cur is None or e.confidence > cur.confidence:
                best[e.pair_key()] = e
        for e in best.values():
            e.active = True

    def set_active_link(
        self, from_table: str, from_col: str, to_table: str, to_col: str
    ) -> bool:
        """Make one edge the active link for its table-pair and deactivate every
        other edge on that pair (enforces the one-active-per-pair invariant).
        Returns False if no matching edge exists."""
        pair = frozenset({from_table, to_table})
        target = None
        for e in self.relationships:
            if e.pair_key() != pair:
                continue
            if (e.from_table, e.from_col, e.to_table, e.to_col) == (
                from_table, from_col, to_table, to_col
            ):
                target = e
        if target is None:
            return False
        for e in self.relationships:
            if e.pair_key() == pair:
                e.active = False
        target.active = True
        return True

    def add_manual_relationship(
        self, from_table: str, from_col: str, to_table: str, to_col: str
    ) -> bool:
        """Add a user-defined join as a real relationship and make it the single
        active link for its table-pair (so it flows into the nl2sql prompt, the
        join-path walk, and the guardrail whitelist exactly like a discovered edge).

        VALIDATED before anything is persisted: both tables and both columns must
        exist and the two tables must differ (single-column cross-table join only).
        Returns False — adding nothing — when invalid, so the caller can reject it.
        An identical existing edge is reused rather than duplicated."""
        if from_table == to_table:
            return False
        tbl_from, tbl_to = self.table(from_table), self.table(to_table)
        if tbl_from is None or tbl_to is None:
            return False
        if tbl_from.column(from_col) is None or tbl_to.column(to_col) is None:
            return False

        exists = any(
            (e.from_table, e.from_col, e.to_table, e.to_col)
            == (from_table, from_col, to_table, to_col)
            for e in self.relationships
        )
        if not exists:
            self.relationships.append(
                RelationshipEdge(
                    from_table=from_table, from_col=from_col,
                    to_table=to_table, to_col=to_col,
                    confidence=1.0, confidence_label="high",
                    provisional=False, active=False,
                )
            )
        # make it the pair's one active link (deactivates any discovered alternative)
        return self.set_active_link(from_table, from_col, to_table, to_col)

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
    #: when the model declines (needs_clarification) but offers a concrete, runnable
    #: alternative ("I can't drop records — want me to SELECT them instead?"), its
    #: single best-guess restated as a question the "Yes — run it" chip re-submits.
    #: None when the clarify has no actionable alternative (e.g. "which column?").
    proposed_action: str | None = None


class MappingFilter(BaseModel):
    """One value-level filter the AI proposes, e.g. BondID = 'BOND1'."""
    column: str
    op: Literal["=", "!=", ">", "<", ">=", "<="] = "="
    value: Any = None


class MappingAlternative(BaseModel):
    """An interpretation the AI considered but didn't commit to — drives clarify.
    `term` is the user phrase; `options` are the real columns/values it could mean."""
    term: str = ""
    options: list[str] = Field(default_factory=list)


class MappingProposal(BaseModel):
    """Tier-2 structured interpretation. The AI proposes how a vague question maps
    to REAL schema elements — it never returns SQL or a final number. The engine
    re-validates this against the schema/data and owns the answer."""
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    filters: list[MappingFilter] = Field(default_factory=list)
    aggregation: str | None = None            # SUM | COUNT | AVG | MIN | MAX | None
    measure: str | None = None                # column the aggregation applies to
    group_by: list[str] = Field(default_factory=list)
    confidence: Literal["high", "low"] = "low"
    alternatives: list[MappingAlternative] = Field(default_factory=list)
    unmappable: bool = False
    reason: str | None = None                 # why unmappable / what it interpreted
    #: when the AI clarifies, its single best-guess restated as a concrete question.
    #: Surfaced on the clarify response so a "Yes — run it" affirmative re-asks THIS
    #: (stateless: it's just a more specific question on the next /ask).
    proposed_question: str | None = None


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
    #: real-schema example questions, returned with a refusal so a dead-end becomes
    #: something the user can click/try (derived from actual table + column names).
    suggestions: list[str] = Field(default_factory=list)
    #: on a clarify, a concrete question the UI's "Yes — run it" chip re-submits.
    #: None when there's no single sensible action to propose (so no chip renders).
    proposed_action: str | None = None
    blocked_reason: str | None = None
    error: str | None = None
    error_kind: str | None = None  # e.g. "rate_limit" — lets the web layer pick an HTTP code
    chart_hint: str | None = None  # deterministic UI hint (single_value | bar | line | table)
    provider_used: str | None = None  # set only when a rate-limit fallback was used
    fallback_note: str | None = None  # small honest note shown when fallback kicked in
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    tables_used: list[str] = Field(default_factory=list)
