"""Request bodies for the web layer (responses are built as plain dicts and run
through clean_json)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    privacy_mode: bool = False
    # a per-session, user-supplied LLM key. Held in memory only, never stored.
    user_key: str | None = None


class AskRequest(BaseModel):
    question: str


class ColumnEdit(BaseModel):
    table: str
    column: str
    meaning: str | None = None
    role: str | None = None
    dtype: str | None = None
    confidence: float | None = None
    provisional: bool | None = None


class DataDictionaryEntry(BaseModel):
    table: str | None = None  # if omitted, applies to the column in every table
    column: str
    description: str


class RelationshipChoice(BaseModel):
    """The user's active-link pick for one connected table-pair. Identifies the
    chosen edge fully; the engine makes it active and deactivates the pair's others."""
    from_table: str
    from_col: str
    to_table: str
    to_col: str


class ManualRelationship(BaseModel):
    """A user-defined join. Single-column, cross-table. Validated against the schema
    before it's persisted; once accepted it becomes its pair's active link."""
    from_table: str
    from_col: str
    to_table: str
    to_col: str


class ConfirmSchemaRequest(BaseModel):
    column_edits: list[ColumnEdit] = Field(default_factory=list)
    data_dictionary: list[DataDictionaryEntry] = Field(default_factory=list)
    relationship_choices: list[RelationshipChoice] = Field(default_factory=list)
    manual_relationships: list[ManualRelationship] = Field(default_factory=list)


class CleaningRule(BaseModel):
    """A custom cleaning rule. v1 implements three types; the typed {type, column,
    params} shape lets deferred types (date_format, find_replace, exclude_column) be
    added later without restructuring.
      - null_token   params={value}             → treat a value as NULL
      - force_type   params={dtype}             → force numeric|date|boolean|text
      - merge_values params={from:[...], to}    → collapse category values
    `table` is optional; omitted → applies to every table that has `column`."""
    type: Literal["null_token", "force_type", "merge_values"]
    column: str
    table: str | None = None
    params: dict = Field(default_factory=dict)


class ApplyRulesRequest(BaseModel):
    rules: list[CleaningRule] = Field(default_factory=list)


class DuplicateDecision(BaseModel):
    """One user decision about a flagged duplicate group/pair. `row_indices` are the
    positional rows reported in the upload flag (exact group or near pair). On
    `remove`, the FIRST index is kept (the representative) and the rest are dropped;
    `keep` leaves every row untouched. Nothing is ever removed without `remove`."""
    table: str
    row_indices: list[int] = Field(default_factory=list)
    action: Literal["keep", "remove"] = "keep"


class ResolveDuplicatesRequest(BaseModel):
    decisions: list[DuplicateDecision] = Field(default_factory=list)
