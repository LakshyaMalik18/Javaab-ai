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
from app.models import ChangeRecord

# ── custom cleaning rules (v1: three already-plumbed types) ───────────────────
# A rule is a typed object {type, column, table?, params} so the deferred v1.1 types
# (date-format, find/replace, exclude-column) slot in here without restructuring.
_VALID_RULE_TYPES = {"null_token", "force_type", "merge_values"}
_VALID_DTYPES = {"numeric", "date", "boolean", "text"}

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
    warnings: list[str] = field(default_factory=list)  # non-fatal, user-facing notices
    table_meta: list[dict] = field(default_factory=list)  # name/row_count/col_count
    # the RAW, post-ingest / pre-clean frames, kept so custom cleaning rules can
    # re-run the engine deterministically without a re-upload. In-memory only; the
    # session wipes these exactly like the cleaned tables (privacy/ephemeral story).
    raw_tables: dict[str, pd.DataFrame] = field(default_factory=dict)


def _ingest_one(filename: str, raw: bytes) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Bytes → ({table_name: raw_df}, warnings). Raises IngestError on bad input.

    `warnings` are non-fatal notices surfaced from ingest (e.g. an Excel formula
    column whose result was never cached and so couldn't be read)."""
    ext = Path(filename).suffix.lower()
    stem = safe_table_name(Path(filename).stem)
    if ext in (".xlsx", ".xls"):
        sheets = ingest_mod.ingest_excel(raw)
        tables = {safe_table_name(name): d["df"] for name, d in sheets.items()}
        warnings = [w for d in sheets.values() for w in d.get("warnings", [])]
        return tables, warnings
    if ext == ".json":
        r = ingest_mod.ingest_json(raw, stem)
        return {stem: r["df"]}, list(r.get("warnings", []))
    r = ingest_mod.ingest_csv(raw, stem)
    return {stem: r["df"]}, list(r.get("warnings", []))


def process_upload(
    files: list[tuple[str, bytes]],
    rules: list[dict] | None = None,
) -> UploadResult:
    """Run the full ingest+clean pipeline over a batch of uploaded files.

    Never raises for bad input — per-file problems are captured in `errors`
    (graceful, user-facing) so one bad file can't sink the whole upload. The raw,
    post-ingest frames are retained on the result so cleaning can later be re-run
    with custom rules via `rebuild_from_raw`."""
    result = UploadResult()
    raw_tables: dict[str, pd.DataFrame] = {}

    for filename, raw in files:
        try:
            ingested, warnings = _ingest_one(filename, raw)
        except IngestError as e:
            result.errors.append(f"{filename}: {e}")
            continue
        except Exception as e:  # malformed JSON / corrupt xlsx etc. — stay graceful
            result.errors.append(f"{filename}: could not read file ({e})")
            continue
        result.warnings.extend(warnings)

        for name, df in ingested.items():
            # avoid clobbering a same-named table from another file
            if name in raw_tables:
                name = f"{name}_{len([t for t in raw_tables if t.startswith(name)]) + 1}"
            raw_tables[name] = df

    result.raw_tables = raw_tables
    _clean_all(result, raw_tables, rules or [])
    return result


def rebuild_from_raw(
    raw_tables: dict[str, pd.DataFrame],
    rules: list[dict] | None = None,
) -> UploadResult:
    """Re-run the clean → canonicalize → profile → discover pipeline over the RETAINED
    raw frames, applying the user's custom rules. Used when a rule is added/changed so
    cleaning re-runs deterministically without a re-upload. Ingest is NOT repeated."""
    result = UploadResult()
    result.raw_tables = raw_tables
    _clean_all(result, raw_tables, rules or [])
    return result


def _clean_all(
    result: UploadResult,
    raw_tables: dict[str, pd.DataFrame],
    rules: list[dict],
) -> None:
    """Clean every raw table (honouring custom rules), then profile + discover joins.

    RULE APPLICATION ORDER (per table) — null-tokens before type-coercion, merges last:
      1. null-token rules  → fed to clean() as extra_null_tokens, so the value becomes
         NULL *before* type inference runs (nulls first, then coerce — the correct order;
         coercing first would read "9999" as a number and never null it).
      2. force-type rules  → fed to clean() as force_types, so coercion targets the
         user's type instead of the inferred one.
      3. automatic fuzzy canonicalization of categorical columns (unchanged).
      4. forced category merges → applied last, on the already-typed/canonicalized
         column, so an explicit "America → USA" wins over the auto guess.
    """
    targets = _resolve_rule_targets(rules, raw_tables)

    for name, raw_df in raw_tables.items():
        try:
            _clean_one_table(result, name, raw_df, targets.get(name, {}))
        except Exception as e:
            # A single table's cleaning blowing up must not sink the whole batch —
            # record a clear per-table message and keep processing the rest.
            result.errors.append(f"{name}: could not process file ({e})")

    # profiler + join discovery run once all tables are built (joins are cross-table)
    for name, df in result.tables.items():
        result.profiles[name] = profiler_mod.profile_table(df, name)
    if result.tables:
        result.relationships = joins_mod.discover_joins(result.tables, result.profiles)


def _clean_one_table(
    result: UploadResult,
    name: str,
    raw_df: pd.DataFrame,
    t: dict,
) -> None:
    """Clean a single raw table (custom rules + canonicalization) and record its
    cleaned frame, ledger, and flags onto `result`. Raising here is caught by the
    caller so one bad table can't take down the rest of the batch."""
    cleaned, ledger, dup_groups, ambiguities = cleaning_mod.clean(
        raw_df,
        table_name=name,
        extra_null_tokens=frozenset(t.get("null_tokens", ())),
        force_types=t.get("force_types") or None,
    )

    # canonicalize categorical text columns (high-confidence merges applied
    # + logged in the same ledger; near-dup rows are flagged, never removed)
    for col in list(cleaned.columns):
        if _is_text_categorical(cleaned[col]):
            cleaned[col], _ = canonical_mod.canonicalize_column(
                cleaned[col], name, col, ledger
            )

    # user-defined category merges (forced canonical mapping), applied last
    for column, from_vals, to_val in t.get("merges", []):
        _apply_forced_merge(cleaned, name, column, from_vals, to_val, ledger)

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


# ── rule validation + resolution ──────────────────────────────────────────────

def validate_rules(
    rules: list[dict], raw_tables: dict[str, pd.DataFrame]
) -> list[dict]:
    """Validate raw rule dicts against the uploaded schema; raise ValueError with a
    clear, user-facing message on the first bad rule so nothing invalid is applied.
    Returns normalized rule dicts ({type, column, table, params})."""
    cols_by_table = {n: set(map(str, df.columns)) for n, df in raw_tables.items()}
    all_cols: set[str] = set().union(*cols_by_table.values()) if cols_by_table else set()

    out: list[dict] = []
    for i, rule in enumerate(rules or []):
        n = i + 1
        rtype = rule.get("type")
        if rtype not in _VALID_RULE_TYPES:
            raise ValueError(f"rule {n}: unknown rule type '{rtype}'")
        column = str(rule.get("column") or "").strip()
        if not column:
            raise ValueError(f"rule {n}: a column is required")
        table = rule.get("table") or None
        if table is not None:
            if table not in cols_by_table:
                raise ValueError(f"rule {n}: unknown table '{table}'")
            if column not in cols_by_table[table]:
                raise ValueError(f"rule {n}: column '{column}' is not in table '{table}'")
        elif column not in all_cols:
            raise ValueError(f"rule {n}: column '{column}' doesn't exist in any uploaded table")

        params = rule.get("params") or {}
        if rtype == "null_token":
            if str(params.get("value", "")).strip() == "":
                raise ValueError(f"rule {n}: the null-token rule needs a non-empty value")
        elif rtype == "force_type":
            if params.get("dtype") not in _VALID_DTYPES:
                raise ValueError(
                    f"rule {n}: force-type needs a dtype of {sorted(_VALID_DTYPES)}"
                )
        elif rtype == "merge_values":
            frm = params.get("from") or []
            if not isinstance(frm, list) or not any(str(v).strip() for v in frm):
                raise ValueError(f"rule {n}: the merge rule needs at least one 'from' value")
            if str(params.get("to", "")).strip() == "":
                raise ValueError(f"rule {n}: the merge rule needs a non-empty 'to' value")

        out.append({"type": rtype, "column": column, "table": table, "params": params})
    return out


def _resolve_rule_targets(
    rules: list[dict], raw_tables: dict[str, pd.DataFrame]
) -> dict[str, dict]:
    """Group validated rules by the table(s) they apply to. A rule with no explicit
    `table` targets every table that has the named column. Returns
    {table: {null_tokens: set, force_types: dict, merges: [(col, from, to)]}}."""
    targets: dict[str, dict] = {}
    for rule in rules or []:
        column, params = rule["column"], rule.get("params", {})
        if rule.get("table"):
            tnames = [rule["table"]]
        else:
            tnames = [n for n, df in raw_tables.items() if column in set(map(str, df.columns))]
        for tn in tnames:
            slot = targets.setdefault(
                tn, {"null_tokens": set(), "force_types": {}, "merges": []}
            )
            if rule["type"] == "null_token":
                # NOTE (v1 granularity): clean()'s extra_null_tokens is table-wide, so
                # the token nulls this value across the table, not just `column`.
                # Per-column scoping is a deferred v1.1 refinement.
                slot["null_tokens"].add(str(params["value"]).strip().lower())
            elif rule["type"] == "force_type":
                slot["force_types"][column] = params["dtype"]
            elif rule["type"] == "merge_values":
                slot["merges"].append(
                    (column, [str(v) for v in params["from"]], str(params["to"]))
                )
    return targets


def _apply_forced_merge(
    df: pd.DataFrame,
    table: str,
    column: str,
    from_vals: list[str],
    to_val: str,
    ledger,
) -> None:
    """Collapse a set of category values into one canonical label (case-insensitive),
    recording the change in the ledger. The user's explicit mapping is authoritative."""
    if column not in df.columns:
        return
    from_set = {v.strip().lower() for v in from_vals if str(v).strip()}
    if not from_set:
        return
    col = df[column]
    norm = col.map(
        lambda v: str(v).strip().lower()
        if v is not None and not (isinstance(v, float) and pd.isna(v))
        else None
    )
    mask = norm.isin(from_set)
    affected = int(mask.sum())
    if affected == 0:
        return
    before = [str(v) for v in col[mask].head(3).tolist()]
    df.loc[mask, column] = to_val
    ledger.add(ChangeRecord(
        table=table, column=column,
        rule=f"merge category values → '{to_val}'",
        cells_affected=affected,
        before_sample=before, after_sample=[to_val],
    ))
