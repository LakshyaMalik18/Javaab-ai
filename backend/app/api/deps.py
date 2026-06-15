"""Shared FastAPI dependencies: locate the process-wide store and the caller's
session (transported via the `X-Session-Id` header)."""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.session import Session, SessionNotFound, SessionStore


def get_store(request: Request) -> SessionStore:
    return request.app.state.store


def get_session(
    request: Request,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> Session:
    store: SessionStore = request.app.state.store
    try:
        return store.get(x_session_id)
    except SessionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
