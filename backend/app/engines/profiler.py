"""
profiler.py — ENGINE 3 (deterministic column profiling, no LLM).

Per-column deterministic stats that feed both the schema-confirm UI and the
value-based join discovery (joins.py). The output is a plain dict per column so
the test harness can read it directly:

    profile_table(df, table_name) -> { column_name: {profile fields...} }

Key fields the rest of the system relies on:
  - is_id   : column is a candidate primary key (unique, non-null, id-shaped)
  - is_fk   : set later by joins.py once a relationship is discovered
  - role    : id | dimension | measure | timestamp | text
"""
from __future__ import annotations

import re
import pandas as pd

# ---- pattern fingerprints -------------------------------------------------
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL = re.compile(r"^https?://", re.I)
_ID_NAME = re.compile(r"(^|_)(id|key|code|no|num)$", re.I)

# columns this few distinct text values are dimensions, not free text
_DIMENSION_CARDINALITY = 0.7


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def _is_integral(series: pd.Series) -> bool:
    """True if a numeric column holds only whole numbers (ids are integral)."""
    non_null = series.dropna()
    if len(non_null) == 0:
        return False
    try:
        return bool((non_null == non_null.round(0)).all())
    except TypeError:
        return False


def _fingerprint(non_null: pd.Series, dtype: str) -> list[str]:
    fp: list[str] = []
    str_vals = [str(v) for v in non_null]
    if not str_vals:
        return fp

    def frac(rx) -> float:
        return sum(1 for v in str_vals if rx.match(v)) / len(str_vals)

    if frac(_EMAIL) >= 0.9:
        fp.append("email")
    if frac(_UUID) >= 0.9:
        fp.append("uuid")
    if frac(_URL) >= 0.9:
        fp.append("url")
    if dtype == "date" or frac(_ISO_DATE) >= 0.9:
        fp.append("iso_date")
    if dtype == "numeric" and _is_integral(non_null):
        fp.append("integer")
    return fp


def _infer_role(
    norm_name: str,
    dtype: str,
    fingerprint: list[str],
    cardinality_ratio: float,
    distinct_count: int,
    is_id_like: bool,
) -> str:
    if dtype == "date" or "iso_date" in fingerprint:
        return "timestamp"
    if is_id_like or _ID_NAME.search(norm_name) and dtype != "text":
        return "id"
    if dtype == "numeric":
        # integral + name says id => id, else a measure
        if _ID_NAME.search(norm_name) and "integer" in fingerprint:
            return "id"
        return "measure"
    if dtype == "boolean":
        return "dimension"
    # text-ish
    if cardinality_ratio <= _DIMENSION_CARDINALITY and distinct_count <= 50:
        return "dimension"
    return "text"


def profile_column(series: pd.Series, raw_name: str) -> dict:
    n = len(series)
    non_null = series.dropna()
    null_pct = (1.0 - len(non_null) / n) if n else 1.0
    distinct_count = int(non_null.nunique())
    cardinality_ratio = (distinct_count / len(non_null)) if len(non_null) else 0.0
    dtype = _series_dtype(series)
    norm_name = _norm_name(raw_name)
    fingerprint = _fingerprint(non_null, dtype)

    # candidate primary key: every value present, every value unique, and shaped
    # like a key (a UUID, or an id-ish name). A bare unique-integer column is NOT
    # assumed to be a key — that would mislabel unique measures (amounts, prices)
    # as PKs. Value-based join discovery uses the separate `_unique` flag instead.
    unique_nonnull = null_pct == 0.0 and distinct_count == len(non_null) and distinct_count > 1
    id_shaped = "uuid" in fingerprint or bool(_ID_NAME.search(norm_name))
    is_id = bool(unique_nonnull and id_shaped)

    role = _infer_role(
        norm_name, dtype, fingerprint, cardinality_ratio, distinct_count, is_id
    )

    prof: dict = {
        "raw_name": raw_name,
        "norm_name": norm_name,
        "dtype": dtype,
        "null_pct": round(null_pct, 4),
        "distinct_count": distinct_count,
        "cardinality_ratio": round(cardinality_ratio, 4),
        "sample_values": _samples(non_null),
        "pattern_fingerprint": fingerprint,
        "role": role,
        "likely_role": role,
        "is_id": is_id,
        "is_fk": False,  # filled in by joins.py
        "_unique": bool(null_pct == 0.0 and distinct_count == len(non_null) and distinct_count > 0),
    }

    if dtype == "numeric" and len(non_null):
        prof["numeric_min"] = float(non_null.min())
        prof["numeric_max"] = float(non_null.max())
    if (dtype == "date" or "iso_date" in fingerprint) and len(non_null):
        as_str = sorted(str(v) for v in non_null)
        prof["date_min"], prof["date_max"] = as_str[0], as_str[-1]
    if dtype in ("text",) and len(non_null):
        prof["avg_len"] = round(sum(len(str(v)) for v in non_null) / len(non_null), 2)

    return prof


def _samples(non_null: pd.Series, k: int = 5) -> list:
    vals = list(dict.fromkeys(non_null.tolist()))  # distinct, order-preserving
    return vals[:k]


def _series_dtype(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    # object column: detect ISO-date text columns
    non_null = series.dropna()
    if len(non_null):
        str_vals = [str(v) for v in non_null]
        if sum(1 for v in str_vals if _ISO_DATE.match(v)) / len(str_vals) >= 0.9:
            return "date"
    return "text"


def profile_table(df: pd.DataFrame, table_name: str) -> dict[str, dict]:
    """Profile every column of a cleaned table. Returns {column: profile dict}."""
    return {col: profile_column(df[col], col) for col in df.columns}
