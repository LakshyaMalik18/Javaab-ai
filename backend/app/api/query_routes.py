"""Ask endpoint — the answer() flow: insight, chart hint, table, SQL, follow-ups,
or a clarifying question / refusal / guardrail block."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api._serialize import chart_hint, clean_json
from app.api.deps import get_session
from app.api.schemas import AskRequest
from app.session import Session, SessionError

router = APIRouter(tags=["query"])


@router.post("/ask")
def ask(body: AskRequest, session: Session = Depends(get_session)):
    if not body.question or not body.question.strip():
        raise HTTPException(status_code=400, detail="question is required")

    try:
        res = session.ask(body.question)
    except SessionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    res.chart_hint = chart_hint(res)
    payload = clean_json(res.model_dump())

    # A model rate-limit becomes a clean 429, never a 500. This is reached only
    # when every available provider is busy: in default mode after the Groq
    # fallback was also rate-limited, or in Privacy Mode where Groq is the only
    # provider and we will NOT route the user's data to an opted-out one.
    if res.status == "error" and res.error_kind == "rate_limit":
        return JSONResponse(status_code=429, content={
            **payload,
            "error": "rate_limited",
            "detail": "The analysis service is busy right now. Please try again in a moment.",
        })

    return payload
