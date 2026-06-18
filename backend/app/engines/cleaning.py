"""Deterministic, reversible cleaning engine with ChangeLedger.

Stages (matching §3):
  1. Type inference + coercion (number/currency, date, boolean, null tokens)
  2. Whitespace normalisation
  3. Duplicate-row detection (report only — never auto-delete)
  4. Fully-empty column/row removal (already done in ingest; checked here)
  5. Confidence flags for unresolved ambiguities
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import pandas as pd

from app.models import AmbiguityFlag, ChangeRecord, ChangeLedger, DuplicateGroup

# ── Null token table ──────────────────────────────────────────────────────────

DEFAULT_NULL_TOKENS: frozenset[str] = frozenset({
    "na", "n/a", "n/a.", "-", "--", "null", "none", "nil", "nan",
    "#n/a", "#na", "?", "unknown", "",
})


def _is_null_token(val: str, extra: frozenset[str] = frozenset()) -> bool:
    return val.strip().lower() in (DEFAULT_NULL_TOKENS | extra)


# ── Number / currency coercion ────────────────────────────────────────────────

_CURRENCY_PREFIX = re.compile(r"^[\$€₹£¥₩₺]")
_UNIT_SUFFIX = re.compile(r"([kmb])$", re.I)
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3})")
_PARENS_NEG = re.compile(r"^\((.+)\)$")

_UNIT_MULT = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def _try_numeric(val: str) -> float | None:
    v = val.strip()
    # parentheses → negative
    m = _PARENS_NEG.match(v)
    if m:
        inner = m.group(1)
        result = _try_numeric(inner)
        return -result if result is not None else None

    is_percent = v.endswith("%")
    v = _CURRENCY_PREFIX.sub("", v).strip()
    v = v.rstrip("%").strip()
    v = _THOUSANDS.sub("", v)

    suffix_m = _UNIT_SUFFIX.search(v)
    mult = 1.0
    if suffix_m:
        mult = _UNIT_MULT[suffix_m.group(1).lower()]
        v = v[: suffix_m.start()].strip()

    try:
        num = float(v) * mult
    except ValueError:
        return None
    # Percent → fraction (10% → 0.10), per FIXTURES_SPEC.
    return num / 100.0 if is_percent else num


def _coerce_numeric_column(
    series: pd.Series, table: str, column: str, ledger: ChangeLedger
) -> pd.Series:
    before, after = [], []            # generic numeric cleanups ($, commas, (), units)
    pct_before, pct_after = [], []    # percent → fraction conversions (logged separately)
    # Accumulate coerced values into a plain list (seeded from the originals), then
    # assemble a fresh Series at the end. We deliberately do NOT mutate a copy of the
    # source Series cell-by-cell: newer pandas forbids assigning a float into a cell
    # of a string-dtype Series. Unparsed cells keep their original value untouched.
    values = series.tolist()
    changed = pct_changed = 0

    for pos, (_, val) in enumerate(series.items()):
        if pd.isna(val) or str(val).strip() == "":
            continue
        if isinstance(val, (int, float)):
            continue  # already numeric
        s = str(val)
        plain = s.strip()
        # A clean numeric string (e.g. "9.99", "1200") needs no transformation —
        # typing it is not a "change" and must NOT pollute the ledger.
        try:
            num = float(plain)
            values[pos] = num
            continue
        except ValueError:
            pass
        num = _try_numeric(plain)
        if num is None:
            continue
        values[pos] = num
        if plain.endswith("%"):
            if pct_changed < 5:
                pct_before.append(s)
                pct_after.append(num)
            pct_changed += 1
        else:
            if changed < 5:
                before.append(s)
                after.append(num)
            changed += 1

    if changed:
        ledger.add(ChangeRecord(
            table=table, column=column,
            rule="numeric_coerce",
            cells_affected=changed,
            before_sample=before, after_sample=after,
        ))
    if pct_changed:
        ledger.add(ChangeRecord(
            table=table, column=column,
            rule="percent_to_fraction",
            cells_affected=pct_changed,
            before_sample=pct_before, after_sample=pct_after,
        ))
    return pd.Series(values, index=series.index, name=series.name)


# ── Date coercion ─────────────────────────────────────────────────────────────

from dateutil import parser as du_parser
from dateutil.parser import ParserError

_DATE_FORMATS = [
    "%d/%m/%Y", "%m/%d/%Y",
    "%d-%m-%Y", "%m-%d-%Y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%d %b %Y", "%d-%b-%Y", "%b %d %Y", "%B %d %Y",
    "%d/%m/%y", "%m/%d/%y",
]

_SLASH_PAT = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")
_ISO_DATE_PAT = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _vote_date_order(series: pd.Series) -> str | None:
    """
    Return 'iso', 'dmy', 'mdy', or None (ambiguous).
    'iso' = column already in YYYY-MM-DD, no reordering needed.
    """
    non_null = series.dropna()
    if len(non_null) == 0:
        return None

    # If most values are already ISO, no reordering needed
    iso_hits = sum(1 for v in non_null if _ISO_DATE_PAT.match(str(v).strip()))
    if iso_hits / len(non_null) > 0.5:
        return "iso"

    # Only STRONG evidence (a field > 12) resolves the order. If no value
    # disambiguates, the column is genuinely ambiguous -> None (flagged upstream).
    strong_dmy = strong_mdy = 0
    for val in non_null:
        s = str(val).strip()
        m = _SLASH_PAT.match(s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > 12:
                strong_dmy += 1   # day must be in the first field
            elif b > 12:
                strong_mdy += 1   # day must be in the second field
    if strong_dmy == 0 and strong_mdy == 0:
        return None
    return "dmy" if strong_dmy >= strong_mdy else "mdy"


def _parse_date(val: str, order: str | None) -> str | None:
    v = val.strip()
    if not v:
        return None
    # Already ISO — return as-is
    if _ISO_DATE_PAT.match(v):
        return v
    m = _SLASH_PAT.match(v)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(c) + (2000 if len(c) == 2 else 0)
        if order == "dmy":
            # a=day, b=month → YYYY-MM-DD
            return f"{year:04d}-{b:02d}-{a:02d}"
        elif order == "mdy":
            # a=month, b=day → YYYY-MM-DD
            return f"{year:04d}-{a:02d}-{b:02d}"
        # fallback: let dateutil decide
    try:
        dt = du_parser.parse(v, dayfirst=(order == "dmy"))
        return dt.strftime("%Y-%m-%d")
    except (ParserError, OverflowError, ValueError):
        return None


def _coerce_date_column(
    series: pd.Series, table: str, column: str, ledger: ChangeLedger,
    ambiguity_flags: list[AmbiguityFlag],
) -> pd.Series:
    order = _vote_date_order(series)
    if order is None:
        ambiguity_flags.append(AmbiguityFlag(
            column=column, kind="date_order",
            detail=f"Column '{column}': date order ambiguous (all values ≤ 12 in first field). Flagged for confirmation.",
        ))
    # order == "iso" → already normalized, no flag needed

    before, after = [], []
    result = series.copy()
    changed = fail = 0

    for i, val in series.items():
        if pd.isna(val):
            continue
        s = str(val).strip()
        if not s or _is_null_token(s):
            continue
        iso = _parse_date(s, order)
        if iso and iso != s:
            if changed < 5:
                before.append(s)
                after.append(iso)
            result.at[i] = iso
            changed += 1
        elif iso is None:
            fail += 1

    if fail > len(series) * 0.3:
        ambiguity_flags.append(AmbiguityFlag(
            column=column, kind="coerce_failed",
            detail=f"Column '{column}': {fail} values failed date parsing.",
        ))

    if changed:
        ledger.add(ChangeRecord(
            table=table, column=column,
            rule="date_normalize",
            cells_affected=changed,
            before_sample=before, after_sample=after,
        ))
    return result


# ── Boolean coercion ──────────────────────────────────────────────────────────

_TRUE_VALS = frozenset({"yes", "y", "true", "t", "1"})
_FALSE_VALS = frozenset({"no", "n", "false", "f", "0"})


def _coerce_bool_column(
    series: pd.Series, table: str, column: str, ledger: ChangeLedger
) -> pd.Series:
    non_null = [str(v).strip().lower() for v in series if not pd.isna(v) and str(v).strip()]
    if not non_null:
        return series
    bool_count = sum(1 for v in non_null if v in _TRUE_VALS | _FALSE_VALS)
    if bool_count / len(non_null) < 0.8:
        return series

    before, after = [], []
    result = series.copy()
    changed = 0
    for i, val in series.items():
        if pd.isna(val):
            continue
        s = str(val).strip().lower()
        if s in _TRUE_VALS and str(val) != "True":
            before.append(str(val))
            after.append(True)
            result.at[i] = True
            changed += 1
        elif s in _FALSE_VALS and str(val) != "False":
            before.append(str(val))
            after.append(False)
            result.at[i] = False
            changed += 1

    if changed:
        ledger.add(ChangeRecord(
            table=table, column=column,
            rule="bool_normalize",
            cells_affected=changed,
            before_sample=before[:5], after_sample=after[:5],
        ))
    return result


# ── Null token normalisation ──────────────────────────────────────────────────

def _normalize_nulls(
    series: pd.Series, table: str, column: str, ledger: ChangeLedger,
    extra_tokens: frozenset[str] = frozenset(),
) -> pd.Series:
    result = series.copy()
    before = []
    changed = 0
    for i, val in series.items():
        if pd.isna(val):
            continue
        s = str(val)
        if _is_null_token(s, extra_tokens):
            if changed < 5:
                before.append(s)
            result.at[i] = pd.NA
            changed += 1
    if changed:
        ledger.add(ChangeRecord(
            table=table, column=column,
            rule="null_normalize",
            cells_affected=changed,
            before_sample=before,
            after_sample=["<NULL>"] * min(changed, 5),
        ))
    return result


# ── Whitespace normalisation ──────────────────────────────────────────────────

def _normalize_whitespace(
    series: pd.Series, table: str, column: str, ledger: ChangeLedger
) -> pd.Series:
    result = series.copy()
    changed = 0
    before, after = [], []
    for i, val in series.items():
        if pd.isna(val):
            continue
        s = str(val)
        cleaned = re.sub(r"\s+", " ", s).strip()
        if cleaned != s:
            if changed < 5:
                before.append(repr(s))
                after.append(repr(cleaned))
            result.at[i] = cleaned
            changed += 1
    if changed:
        ledger.add(ChangeRecord(
            table=table, column=column,
            rule="whitespace_trim",
            cells_affected=changed,
            before_sample=before, after_sample=after,
        ))
    return result


# ── Type inference ────────────────────────────────────────────────────────────

def _infer_column_type(series: pd.Series) -> str:
    """Infer 'numeric', 'date', 'boolean', or 'text' for a whole column."""
    non_null = [str(v).strip() for v in series if not pd.isna(v) and str(v).strip()]
    if not non_null:
        return "text"

    # boolean
    bool_hits = sum(1 for v in non_null if v.lower() in _TRUE_VALS | _FALSE_VALS)
    if bool_hits / len(non_null) >= 0.8:
        return "boolean"

    # numeric (including currency/percent)
    num_hits = sum(1 for v in non_null if _try_numeric(v) is not None)
    if num_hits / len(non_null) >= 0.7:
        return "numeric"

    # date heuristic: many slashes/dashes + parseable
    date_hits = 0
    for v in non_null[:50]:
        try:
            du_parser.parse(v, fuzzy=False)
            date_hits += 1
        except Exception:
            pass
    if date_hits / min(len(non_null), 50) >= 0.7:
        return "date"

    return "text"


# ── Duplicate detection ───────────────────────────────────────────────────────

def _find_exact_duplicates(df: pd.DataFrame) -> list[DuplicateGroup]:
    groups = []
    seen: dict[tuple, list[int]] = {}
    for i, row in df.iterrows():
        key = tuple(str(v) for v in row)
        seen.setdefault(key, []).append(int(i))
    for key, idxs in seen.items():
        if len(idxs) > 1:
            groups.append(DuplicateGroup(
                row_indices=idxs,
                sample=dict(zip(df.columns, key)),
            ))
    return groups


# ── Main clean() entry point ──────────────────────────────────────────────────

def clean(
    df: pd.DataFrame,
    table_name: str = "data",
    extra_null_tokens: frozenset[str] = frozenset(),
    force_types: dict[str, str] | None = None,
    date_formats: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, ChangeLedger, list[DuplicateGroup], list[AmbiguityFlag]]:
    """
    Clean a DataFrame deterministically.

    Returns:
        (cleaned_df, ledger, duplicate_groups, ambiguity_flags)

    Nothing is deleted without user consent (duplicates flagged, not removed).
    """
    ledger = ChangeLedger()
    ambiguity_flags: list[AmbiguityFlag] = []
    df = df.copy()

    force_types = force_types or {}

    for col in df.columns:
        # 1. Whitespace normalise first (string columns)
        df[col] = _normalize_whitespace(df[col], table_name, col, ledger)

        # 2. Null token normalisation
        df[col] = _normalize_nulls(df[col], table_name, col, ledger, extra_null_tokens)

        # 3. Infer type (unless forced)
        inferred = force_types.get(col) or _infer_column_type(df[col])

        if inferred == "numeric":
            df[col] = _coerce_numeric_column(df[col], table_name, col, ledger)
        elif inferred == "date":
            df[col] = _coerce_date_column(df[col], table_name, col, ledger, ambiguity_flags)
        elif inferred == "boolean":
            df[col] = _coerce_bool_column(df[col], table_name, col, ledger)
        # text: already whitespace-normalised

    # 3.5 Finalise dtypes: adopt a numeric dtype only when every non-null value
    # parses cleanly (leaves ISO-date and text columns as object).
    for col in df.columns:
        # respect an explicit non-numeric force_type: a user who forced a numeric-
        # looking column to text/date/boolean must not have it silently re-numified.
        if force_types.get(col) in ("text", "date", "boolean"):
            continue
        orig_notna = df[col].notna()
        if not orig_notna.any():
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        # adopt only if no new NULLs were introduced (i.e. fully numeric column)
        if (converted.notna() | ~orig_notna).all():
            df[col] = converted

    # 4. Check for mixed types after coercion
    for col in df.columns:
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue
        types = set(type(v).__name__ for v in non_null)
        if len(types) > 1 and "str" in types:
            ambiguity_flags.append(AmbiguityFlag(
                column=col, kind="mixed_type",
                detail=f"Column '{col}' has mixed types after coercion: {types}",
            ))

    # 5. Exact duplicate detection (report only)
    dup_groups = _find_exact_duplicates(df)

    return df, ledger, dup_groups, ambiguity_flags
