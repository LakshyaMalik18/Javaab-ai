"""Upload endpoint — multi-file CSV/XLSX/JSON → cleaning report + change ledger."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api._serialize import clean_json
from app.api.deps import get_session
from app.session import Session, manual_join_reset_warning
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

    # a re-upload rebuilds the contract → any user-defined manual joins on it are
    # discarded. Capture them BEFORE load_upload wipes the contract so we can tell the
    # user instead of dropping them silently. (Empty on a first upload — nothing to lose.)
    warnings = manual_join_reset_warning(session.manual_join_labels(), "Re-uploading")

    up = process_upload(payloads)
    session.load_upload(up)

    # ingest-time notices (e.g. an Excel formula column with no cached value) ride
    # the same warnings channel as the manual-join reset notice.
    warnings = warnings + up.warnings

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
        "warnings": warnings,
    }
    return clean_json(report)
