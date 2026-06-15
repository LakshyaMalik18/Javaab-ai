"""Metrics endpoint — live trust-panel numbers from the guardrail log. Metadata
only; no user data is ever counted or returned here."""
from __future__ import annotations

from fastapi import APIRouter, Header, Request

from app.api._serialize import clean_json
from app.session import SessionStore

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics(
    request: Request,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
):
    store: SessionStore = request.app.state.store
    store.sweep()

    session_block = None
    if x_session_id:
        sess = store._sessions.get(x_session_id)
        if sess is not None and not sess.closed:
            sess.touch()
            m = sess.metrics
            session_block = {
                "session_id": sess.id,
                "queries_answered": sess.queries_answered,
                "queries_total": sess.queries_total,
                "guardrail_allowed": m.allowed,
                "guardrail_blocked": m.blocked,
                "destructive_blocked_pct": m.pct_blocked if m.total else 100.0,
                "tables_loaded": len(sess.tables),
                "data_retention": "0 bytes after session",
            }

    return clean_json({
        "session": session_block,
        "aggregate": store.aggregate(),
        "data_retention": "0 bytes after session",
    })
