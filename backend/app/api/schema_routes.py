"""Schema endpoints — review the confidence contract + relationship graph (GET)
and accept user edits / a data dictionary (POST)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api._serialize import clean_json
from app.api.deps import get_session
from app.api.schemas import ConfirmSchemaRequest
from app.session import Session, SessionError

router = APIRouter(tags=["schema"])

_CONFIRMED_CONFIDENCE = 0.99  # a human/dictionary statement overrides the AI guess


def _contract_payload(session: Session) -> dict:
    c = session.contract
    return clean_json({
        "session_id": session.id,
        "tables": [t.model_dump() for t in c.tables],
        "relationships": [r.model_dump() for r in c.relationships],
    })


@router.get("/schema")
def get_schema(session: Session = Depends(get_session)):
    try:
        session.ensure_contract()
    except SessionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _contract_payload(session)


@router.post("/confirm-schema")
def confirm_schema(body: ConfirmSchemaRequest, session: Session = Depends(get_session)):
    try:
        contract = session.ensure_contract()
    except SessionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    applied: list[str] = []

    # 1. explicit per-column edits (type/role/meaning/confidence/provisional)
    for edit in body.column_edits:
        tbl = contract.table(edit.table)
        col = tbl.column(edit.column) if tbl else None
        if col is None:
            raise HTTPException(status_code=400, detail=f"unknown column {edit.table}.{edit.column}")
        if edit.meaning is not None:
            col.meaning = edit.meaning
        if edit.role is not None:
            col.role = edit.role
        if edit.dtype is not None:
            col.dtype = edit.dtype
        if edit.confidence is not None:
            col.confidence = max(0.0, min(1.0, edit.confidence))
        # an explicit edit resolves ambiguity unless the user re-flags it
        col.provisional = edit.provisional if edit.provisional is not None else False
        if not col.provisional:
            col.clarifying_question = None
        applied.append(f"{edit.table}.{edit.column}")

    # 2. data dictionary — descriptions override AI meanings (§4)
    for entry in body.data_dictionary:
        for tbl in contract.tables:
            if entry.table is not None and tbl.name != entry.table:
                continue
            col = tbl.column(entry.column)
            if col is None:
                continue
            col.meaning = entry.description
            col.confidence = _CONFIRMED_CONFIDENCE
            col.provisional = False
            col.clarifying_question = None
            applied.append(f"{tbl.name}.{entry.column}")

    # 3. relationship active-link choices — exactly one active edge per table-pair.
    # Only the active link is used at query time (nl2sql prompt + guardrail), so this
    # is the user's sole lever over joins. set_active_link enforces one-per-pair.
    for choice in body.relationship_choices:
        ok = contract.set_active_link(
            choice.from_table, choice.from_col, choice.to_table, choice.to_col
        )
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"no discovered link {choice.from_table}.{choice.from_col} -> "
                    f"{choice.to_table}.{choice.to_col} to activate"
                ),
            )
        applied.append(f"join:{choice.from_table}~{choice.to_table}")

    payload = _contract_payload(session)
    payload["applied"] = applied
    return payload
