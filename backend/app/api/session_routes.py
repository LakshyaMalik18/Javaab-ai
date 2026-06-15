"""Session lifecycle endpoints — create and wipe."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.deps import get_store
from app.api.schemas import CreateSessionRequest
from app.session import SessionStore

router = APIRouter(tags=["session"])


@router.post("/session")
def create_session(body: CreateSessionRequest | None = None, store: SessionStore = Depends(get_store)):
    body = body or CreateSessionRequest()
    sess = store.create(privacy_mode=body.privacy_mode, user_key=body.user_key)
    return {
        "session_id": sess.id,
        "privacy_mode": sess.privacy_mode,
        "provider": sess.provider.name,
        "created_at": sess.created_at,
        "timeout_seconds": sess.timeout_seconds,
        "data_retention": "in-memory only; wiped on session end",
    }


@router.delete("/session")
def end_session(
    request: Request,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
):
    """Explicit wipe: closes the session's DuckDB and drops all user data."""
    store: SessionStore = request.app.state.store
    wiped = store.close(x_session_id) if x_session_id else False
    if not wiped:
        raise HTTPException(status_code=404, detail="unknown or already-wiped session")
    return {"session_id": x_session_id, "wiped": True, "data_retention": "0 bytes after session"}
