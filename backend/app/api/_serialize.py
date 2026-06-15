"""JSON-safety helpers for the web layer.

DuckDB/pandas results carry numpy scalars, NaN/NaT and Timestamps that don't
round-trip through strict JSON. `clean_json` normalises them so every endpoint
returns valid JSON. `chart_hint` derives a *deterministic* UI hint from a result
shape (no LLM call) — §6 lists a chart between insight and table."""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

from app.models import AnswerResult

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


def clean_json(obj):
    """Recursively coerce numpy / pandas / non-finite values into JSON-safe ones."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean_json(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, np.ndarray):
        return [clean_json(v) for v in obj.tolist()]
    try:
        na = pd.isna(obj)
        if isinstance(na, (bool, np.bool_)) and bool(na):
            return None
    except (ValueError, TypeError):
        pass
    return obj


def chart_hint(res: AnswerResult) -> str | None:
    """single_value | bar | line | table — a cheap, deterministic suggestion."""
    if res.status != "answered" or not res.rows:
        return None
    cols = res.columns
    if len(res.rows) == 1 and len(cols) == 1:
        return "single_value"
    row0 = res.rows[0]

    def _is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def _is_date(v):
        return isinstance(v, str) and bool(_DATE_PREFIX.match(v))

    numeric = [c for c in cols if _is_num(row0.get(c))]
    date_cols = [c for c in cols if _is_date(row0.get(c))]
    if date_cols and numeric:
        return "line"
    if len(cols) == 2 and len(numeric) == 1:
        return "bar"
    return "table"
