"""
main.py — FastAPI entry for Javaab's web layer (Phase 4).

Wraps the proven engines behind a thin HTTP surface:
  POST   /session         create a session (privacy_mode + optional BYO key)
  DELETE /session         explicit wipe — closes the in-memory DuckDB
  POST   /upload          multi-file CSV/XLSX/JSON → cleaning report + ledger
  GET    /schema          the confidence contract + relationship graph
  POST   /confirm-schema  accept user edits / data dictionary
  POST   /ask             answer() → insight, chart hint, table, SQL, follow-ups
  GET    /metrics         live trust-panel numbers (metadata only)

CORS is env-driven (locked to the deployed frontend in prod). LLM provider errors
are turned into clean HTTP responses — a rate limit is a 429, never a 500.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    metrics_routes,
    query_routes,
    schema_routes,
    session_routes,
    upload_routes,
)
from app.llm.base import LLMConfigError, LLMError, RateLimitError
from app.session import SessionStore

load_dotenv()


def _cors_origins() -> list[str]:
    raw = os.environ.get("JAVAAB_CORS_ORIGINS", "").strip()
    if not raw:
        return ["*"]  # permissive for local dev; lock down via env in prod
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    app.state.store.close_all()  # wipe everything on process exit


def create_app() -> FastAPI:
    app = FastAPI(title="Javaab API", version="0.1.0", lifespan=_lifespan)
    app.state.store = SessionStore()

    origins = _cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── provider error → clean HTTP (never a 500) ──────────────────────────────
    @app.exception_handler(RateLimitError)
    async def _rate_limited(_: Request, exc: RateLimitError):
        return JSONResponse(status_code=429, content={
            "error": "rate_limited",
            "detail": f"The model provider is rate-limiting requests. {exc}".strip(),
        })

    @app.exception_handler(LLMConfigError)
    async def _llm_config(_: Request, exc: LLMConfigError):
        return JSONResponse(status_code=400, content={"error": "llm_config", "detail": str(exc)})

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError):
        return JSONResponse(status_code=502, content={"error": "llm_error", "detail": str(exc)})

    @app.get("/health")
    def health():
        return {"status": "ok", "active_sessions": app.state.store.active_count}

    app.include_router(session_routes.router)
    app.include_router(upload_routes.router)
    app.include_router(schema_routes.router)
    app.include_router(query_routes.router)
    app.include_router(metrics_routes.router)

    return app


app = create_app()
