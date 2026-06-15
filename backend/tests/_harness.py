"""
_harness.py — the ONE place the tests touch the engine.

The tests assert on BEHAVIOR (cleaned values, ledger entries, flags, relationships),
never on internal module layout. They all call `run_pipeline(...)` and read a
`CleanResult`. That means Claude Code only has to wire ONE function to the real
engines (in §2/§3/§5 of CLAUDE.md) and every test lights up.

>>> Claude Code's job: implement `_run_real(...)` and set HARNESS_WIRED = True. <<<

Until then every test fails with a clear "harness not wired" message — that is the
intended red state of test-first development.
"""
from __future__ import annotations
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import pandas as pd

# Make the backend package importable regardless of how pytest is invoked.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

FIXTURES_DIR = Path(__file__).parent / "fixtures_audit"

# Flip this to True once _run_real is implemented.
HARNESS_WIRED = True


@dataclass
class CleanResult:
    """What every engine run returns. Populate as much as each engine produces."""
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)       # cleaned, typed, by table name
    ledger: list[dict] = field(default_factory=list)                    # {table, column, rule, cells_affected, before_sample, after_sample}
    flags: list[dict] = field(default_factory=list)                     # {table, column, kind, provisional}  kind e.g. "ambiguous_date","exact_duplicate","near_duplicate"
    relationships: list[dict] = field(default_factory=list)             # {from_table, from_col, to_table, to_col, confidence}
    profiles: dict[str, dict[str, dict]] = field(default_factory=dict)  # table -> column -> {role, is_id, is_fk, ...}
    errors: list[str] = field(default_factory=list)                     # graceful, user-facing failure messages
    raised: bool = False                                                # True only if an UNHANDLED exception escaped (should never happen)

    # ---- convenience accessors used by the tests ----
    def table(self, name: str) -> pd.DataFrame:
        assert name in self.tables, f"expected table '{name}', got {list(self.tables)}"
        return self.tables[name]

    def ledger_for(self, table: str, column: str) -> list[dict]:
        return [e for e in self.ledger if e.get("table") == table and e.get("column") == column]

    def has_flag(self, kind: str, table: str | None = None, column: str | None = None) -> bool:
        for f in self.flags:
            if f.get("kind") != kind:
                continue
            if table is not None and f.get("table") != table:
                continue
            if column is not None and f.get("column") != column:
                continue
            return True
        return False

    def relationship(self, from_table, from_col, to_table, to_col) -> dict | None:
        # accept either direction — join direction normalization is the engine's call
        for r in self.relationships:
            pair = {(r.get("from_table"), r.get("from_col")), (r.get("to_table"), r.get("to_col"))}
            if {(from_table, from_col), (to_table, to_col)} == pair:
                return r
        return None


def run_pipeline(fixture: str, files: list[str] | None = None) -> CleanResult:
    """
    Load every data file in fixtures/<fixture>/ (or just `files`) and run the
    full pipeline available so far. Must NEVER raise for bad input — capture
    problems in `errors` and set tables it could build.
    """
    if not HARNESS_WIRED:
        raise NotImplementedError(
            "Harness not wired yet. Implement _run_real(...) in _harness.py and set "
            "HARNESS_WIRED = True so it calls the real ingest/cleaning/canonical/"
            "profiler/joins engines. This is the expected red state before the engines exist."
        )
    return _run_real(fixture, files)


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_text_categorical(series: pd.Series) -> bool:
    """True for free-text/categorical columns worth canonicalizing —
    excludes numeric columns and ISO-date columns (so dates never get fuzzy-merged)."""
    non_null = series.dropna()
    if len(non_null) == 0:
        return False
    if pd.api.types.is_numeric_dtype(series):
        return False
    str_vals = [str(v) for v in non_null]
    iso_hits = sum(1 for v in str_vals if _ISO_DATE.match(v))
    return iso_hits / len(str_vals) <= 0.5


def _load_file(path: Path) -> dict[str, pd.DataFrame]:
    """Ingest one file into {table_name: raw_df}. Raises IngestError on bad input."""
    from app.engines import ingest as ingest_mod

    raw = path.read_bytes()
    ext = path.suffix.lower()
    stem = path.stem.lower()
    if ext in (".xlsx", ".xls"):
        sheets = ingest_mod.ingest_excel(raw)
        return {name.lower(): d["df"] for name, d in sheets.items()}
    if ext == ".json":
        return {stem: ingest_mod.ingest_json(raw, stem)["df"]}
    return {stem: ingest_mod.ingest_csv(raw, stem)["df"]}


def _run_real(fixture: str, files: list[str] | None) -> CleanResult:
    from app.engines import cleaning as cleaning_mod
    from app.engines import canonical as canonical_mod
    from app.engines import profiler as profiler_mod
    from app.engines import joins as joins_mod
    from app.engines.ingest import IngestError

    fixture_dir = FIXTURES_DIR / fixture
    if files:
        paths = [fixture_dir / f for f in files]
    else:
        paths = sorted(
            p for p in fixture_dir.iterdir()
            if p.is_file() and not p.name.startswith(("_", "."))
        )

    result = CleanResult()
    try:
        for p in paths:
            try:
                raw_tables = _load_file(p)
            except IngestError as e:
                result.errors.append(str(e))          # graceful, user-facing
                continue

            for name, df in raw_tables.items():
                cleaned, ledger, dup_groups, ambiguities = cleaning_mod.clean(df, table_name=name)

                # Canonicalize categorical text columns (high-confidence merges applied,
                # logged in the same ledger; near-dup rows are flagged, never removed).
                for col in list(cleaned.columns):
                    if _is_text_categorical(cleaned[col]):
                        cleaned[col], _ = canonical_mod.canonicalize_column(
                            cleaned[col], name, col, ledger
                        )

                result.tables[name] = cleaned

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
                            "kind": "ambiguous_date", "provisional": True,
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
                    })

        # Profiler + join discovery run once all tables are built (joins are
        # cross-table, so they need the full set in hand).
        for name, df in result.tables.items():
            result.profiles[name] = profiler_mod.profile_table(df, name)
        result.relationships = joins_mod.discover_joins(result.tables, result.profiles)
    except Exception as e:                    # absolute backstop: never let it escape
        result.raised = True
        result.errors.append(f"unexpected: {e}")

    return result
