"""Upload endpoint — multi-file CSV/XLSX/JSON → cleaning report + change ledger."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api._serialize import clean_json
from app.api.deps import get_session
from app.session import Session
from app.upload_pipeline import process_upload

router = APIRouter(tags=["upload"])

_ALLOWED = (".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json")


@router.post("/upload")
async def upload(
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
):
    if not files:
        raise HTTPException(status_code=400, detail="no files provided")

    payloads: list[tuple[str, bytes]] = []
    for f in files:
        name = f.filename or "upload"
        if not name.lower().endswith(_ALLOWED):
            raise HTTPException(
                status_code=415,
                detail=f"unsupported file type: {name} (allowed: {', '.join(_ALLOWED)})",
            )
        payloads.append((name, await f.read()))

    up = process_upload(payloads)
    session.load_upload(up)

    if not session.tables and up.errors:
        # every file failed — surface clearly, but don't 500
        raise HTTPException(status_code=422, detail={"errors": up.errors})

    report = {
        "session_id": session.id,
        "tables": session.table_meta,
        "ledger": {
            "total_cells_affected": sum(r["cells_affected"] for r in session.ledger),
            "records": session.ledger,
        },
        "flags": session.flags,
        "errors": session.errors,
    }
    return clean_json(report)
