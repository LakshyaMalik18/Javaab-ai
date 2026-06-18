"""
upload_pipeline.py — the production upload path.

Wires the already-proven deterministic engines (ingest → clean → canonicalize →
profile → joins) over *uploaded bytes* instead of fixture files. This is pure
orchestration: every line of real logic lives in app/engines/*. It mirrors the
test harness `_run_real` exactly so behaviour is identical to the audited suite.

Nothing here writes to disk — bytes go in, DataFrames come out in memory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.engines import canonical as canonical_mod
from app.engines import cleaning as cleaning_mod
from app.engines import ingest as ingest_mod
from app.engines import joins as joins_mod
from app.engines import profiler as profiler_mod
from app.engines.ingest import IngestError

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IDENT_NONWORD = re.compile(r"[^a-z0-9_]+")


def safe_table_name(raw: str) -> str:
    """Turn a raw filename stem / sheet name into a SQL-safe identifier.

    This is the SINGLE source of a table's name: the value returned here becomes
    the dict key used for DuckDB registration, profiling, the schema contract, the
    name shown to the model, and therefore the name in generated SQL. Sanitizing
    once here keeps all of those identical end-to-end, so the model can never be
    shown a name it can't faithfully write as a bare identifier (e.g. "events 2024"
    → "events_2024", never silently normalised to "events" by the model)."""
    name = (raw or "").strip().lower()
    name = _IDENT_NONWORD.sub("_", name)  # spaces, hyphens, dots, punctuation → _
    name = name.strip("_")
    if not name:
        name = "table"
    if name[0].isdigit():  # a bare leading digit isn't a valid identifier
        name = f"t_{name}"
    return name


def _is_text_categorical(series: pd.Series) -> bool:
    """True for free-text/categorical columns worth canonicalizing — excludes
    numeric and ISO-date columns so dates never get fuzzy-merged. (Identical to
    the test harness rule.)"""
    non_null = series.dropna()
    if len(non_null) == 0:
        return False
    if pd.api.types.is_numeric_dtype(series):
        return False
    str_vals = [str(v) for v in non_null]
    iso_hits = sum(1 for v in str_vals if _ISO_DATE.match(v))
    return iso_hits / len(str_vals) <= 0.5


@dataclass
class UploadResult:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    ledger: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    profiles: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    table_meta: list[dict] = field(default_factory=list)  # name/row_count/col_count


def _ingest_one(filename: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """Bytes → {table_name: raw_df}. Raises IngestError on bad input."""
    ext = Path(filename).suffix.lower()
    stem = safe_table_name(Path(filename).stem)
    if ext in (".xlsx", ".xls"):
        sheets = ingest_mod.ingest_excel(raw)
        return {safe_table_name(name): d["df"] for name, d in sheets.items()}
    if ext == ".json":
        return {stem: ingest_mod.ingest_json(raw, stem)["df"]}
    return {stem: ingest_mod.ingest_csv(raw, stem)["df"]}


def process_upload(files: list[tuple[str, bytes]]) -> UploadResult:
    """Run the full ingest+clean pipeline over a batch of uploaded files.

    Never raises for bad input — per-file problems are captured in `errors`
    (graceful, user-facing) so one bad file can't sink the whole upload."""
    result = UploadResult()

    for filename, raw in files:
        try:
            raw_tables = _ingest_one(filename, raw)
        except IngestError as e:
            result.errors.append(f"{filename}: {e}")
            continue
        except Exception as e:  # malformed JSON / corrupt xlsx etc. — stay graceful
            result.errors.append(f"{filename}: could not read file ({e})")
            continue

        for name, df in raw_tables.items():
            # avoid clobbering a same-named table from another file
            if name in result.tables:
                name = f"{name}_{len([t for t in result.tables if t.startswith(name)]) + 1}"

            cleaned, ledger, dup_groups, ambiguities = cleaning_mod.clean(df, table_name=name)

            # canonicalize categorical text columns (high-confidence merges applied
            # + logged in the same ledger; near-dup rows are flagged, never removed)
            for col in list(cleaned.columns):
                if _is_text_categorical(cleaned[col]):
                    cleaned[col], _ = canonical_mod.canonicalize_column(
                        cleaned[col], name, col, ledger
                    )

            result.tables[name] = cleaned
            result.table_meta.append({
                "name": name,
                "row_count": int(len(cleaned)),
                "col_count": int(len(cleaned.columns)),
                "columns": [str(c) for c in cleaned.columns],
            })

            for rec in ledger.records:
                result.ledger.append({
                    "table": rec.table, "column": rec.column, "rule": rec.rule,
                    "cells_affected": rec.cells_affected,
                    "before_sample": rec.before_sample, "after_sample": rec.after_sample,
                })

            for f in ambiguities:
                if f.kind == "date_order":
                    result.flags.append({
                        "table": name, "column": f.column,
                        "kind": "ambiguous_date", "provisional": True, "detail": f.detail,
                    })
                elif f.kind in ("coerce_failed", "mixed_type"):
                    result.flags.append({
                        "table": name, "column": f.column,
                        "kind": f.kind, "provisional": True, "detail": f.detail,
                    })

            if dup_groups:
                result.flags.append({
                    "table": name, "kind": "exact_duplicate",
                    "groups": [g.row_indices for g in dup_groups],
                })

            near = canonical_mod.find_near_duplicate_rows(cleaned)
            if near:
                result.flags.append({
                    "table": name, "kind": "near_duplicate",
                    "pairs": [n["indices"] for n in near],
                    # per-pair "why": which text field(s) differ, with both values,
                    # so the UI can explain why a pair is near (not exact).
                    "diffs": [
                        {
                            "indices": n["indices"],
                            "fields": n.get("diff_fields", []),
                            "values": {
                                f: [
                                    str(n["sample"]["a"].get(f)),
                                    str(n["sample"]["b"].get(f)),
                                ]
                                for f in n.get("diff_fields", [])
                            },
                        }
                        for n in near
                    ],
                })

    # profiler + join discovery run once all tables are built (joins are cross-table)
    for name, df in result.tables.items():
        result.profiles[name] = profiler_mod.profile_table(df, name)
    if result.tables:
        result.relationships = joins_mod.discover_joins(result.tables, result.profiles)

    return result
