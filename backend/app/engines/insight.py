"""
insight.py — §6.3 the single MERGED LLM call.

One call turns (question + SQL + the top result rows) into both the plain-English
insight that leads the answer AND 2-3 follow-up questions. Merging them keeps us
at <= 2 LLM calls per question on the free tier (the other being nl2sql; schema
labelling is cached once per session).
"""
from __future__ import annotations

import json

import pandas as pd

from app.llm.base import LLMError, LLMProvider
from app.models import InsightResult

SYSTEM_TAG = "ROLE: javaab-insight"

_MAX_ROWS = 10

_SYSTEM = f"""{SYSTEM_TAG}
You are an executive analyst. Given a question, the SQL that answered it, and the
top result rows, write a SHORT plain-English insight (1-3 sentences, lead with the
answer, use real numbers from the rows) and 2-3 natural follow-up questions a
curious analyst would ask next. Return STRICT JSON only:
{{"insight": "<plain english>", "followups": ["<q1>", "<q2>"]}}
Do not mention SQL or tables. If the result is empty, say so plainly."""


def generate_insight(
    question: str,
    sql: str,
    result: pd.DataFrame,
    provider: LLMProvider,
    *,
    max_tokens: int = 500,
) -> InsightResult:
    """Merged insight + follow-ups. Never raises on a model hiccup — returns a
    minimal deterministic fallback so the user still gets their data."""
    head = result.head(_MAX_ROWS)
    rows = head.to_dict(orient="records")
    user = (
        f"Question: {question}\n"
        f"SQL: {sql}\n"
        f"Result rows (up to {_MAX_ROWS}): {json.dumps(rows, default=str)}\n"
        f"Total rows returned: {len(result)}\n\n"
        "Return JSON only."
    )
    try:
        raw = provider.complete_json(_SYSTEM, user, max_tokens=max_tokens)
    except LLMError:
        return InsightResult(
            insight=f"Query returned {len(result)} row(s).",
            followups=[],
        )

    followups = [str(f) for f in (raw.get("followups") or [])][:3]
    return InsightResult(
        insight=str(raw.get("insight") or f"Query returned {len(result)} row(s)."),
        followups=followups,
    )
