"""Request bodies for the web layer (responses are built as plain dicts and run
through clean_json)."""
from __future__ import annotations

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


class ConfirmSchemaRequest(BaseModel):
    column_edits: list[ColumnEdit] = Field(default_factory=list)
    data_dictionary: list[DataDictionaryEntry] = Field(default_factory=list)
    relationship_choices: list[RelationshipChoice] = Field(default_factory=list)
