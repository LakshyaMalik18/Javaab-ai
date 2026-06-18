"""Cleaning-resolution endpoints — explicit, user-driven edits to the cleaned data.

Currently: /resolve-duplicates removes the duplicate rows the user chose to drop.
Removal only ever happens on an explicit `remove` decision; nothing is auto-deleted.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api._serialize import clean_json
from app.api.deps import get_session
from app.api.schemas import ApplyRulesRequest, ResolveDuplicatesRequest
from app.session import Session, SessionError

router = APIRouter(tags=["cleaning"])


@router.post("/apply-rules")
def apply_rules(body: ApplyRulesRequest, session: Session = Depends(get_session)):
    try:
        result = session.apply_cleaning_rules([r.model_dump() for r in body.rules])
    except SessionError as e:
        # invalid rule or no data yet — clear, user-facing message, nothing applied
        raise HTTPException(status_code=400, detail=str(e))
    return clean_json(result)


@router.post("/resolve-duplicates")
def resolve_duplicates(
    body: ResolveDuplicatesRequest, session: Session = Depends(get_session)
):
    try:
        result = session.remove_duplicate_rows(
            [d.model_dump() for d in body.decisions]
        )
    except SessionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return clean_json(result)
