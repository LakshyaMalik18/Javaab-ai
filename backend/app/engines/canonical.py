"""Canonicalization + near-duplicate detection using rapidfuzz."""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd
from rapidfuzz import fuzz, process, utils as rf_utils

from app.models import ChangeRecord, ChangeLedger

# Built-in alias map for common domains
_ALIAS_MAP: dict[str, str] = {
    # USA variants
    "usa": "United States", "u.s.a.": "United States", "u.s.a": "United States",
    "us": "United States", "america": "United States",
    "united states of america": "United States", "united states": "United States",
    # UK variants
    "uk": "United Kingdom", "u.k.": "United Kingdom", "britain": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom",
    # Common status
    "active": "active", "inactive": "inactive",
}


def _canonical_key(val: str) -> str:
    return val.strip().lower()


def canonicalize_column(
    series: pd.Series,
    table: str,
    column: str,
    ledger: ChangeLedger,
    threshold: int = 85,
    max_cardinality: int = 500,
) -> tuple[pd.Series, list[dict[str, Any]]]:
    """
    Cluster similar string values and propose/apply canonical labels.

    Returns:
        (updated_series, suggestions)
        suggestions = [{"original": [...], "canonical": str, "confidence": "high"|"medium"}]
    """
    non_null = series.dropna().astype(str)
    distinct = list(non_null.unique())

    if len(distinct) > max_cardinality:
        distinct = distinct[:max_cardinality]  # cap for performance

    # Apply built-in alias map first (high confidence)
    alias_applied: dict[str, str] = {}
    for val in distinct:
        k = _canonical_key(val)
        if k in _ALIAS_MAP:
            alias_applied[val] = _ALIAS_MAP[k]

    # Fuzzy clustering for the rest
    remaining = [v for v in distinct if v not in alias_applied]
    clusters: list[list[str]] = []
    assigned: set[str] = set()

    for val in remaining:
        if val in assigned:
            continue
        matches = process.extract(
            val, remaining, scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold, limit=50
        )
        group = [m[0] for m in matches if m[0] not in assigned]
        if len(group) > 1:
            clusters.append(group)
            assigned.update(group)

    counts = non_null.value_counts()

    # Case/whitespace normalization (high confidence, auto-applied): values that
    # are identical apart from casing or surrounding whitespace are one value —
    # "paid"/"Paid"/"PAID" must collapse so a GROUP BY / WHERE can't silently
    # split or drop them. This is the same principle as the country alias merge;
    # here the canonical label is the most frequent spelling in the column.
    case_groups: dict[str, list[str]] = {}
    for val in distinct:
        if val in alias_applied:
            continue  # alias map already gives these a canonical form
        case_groups.setdefault(_canonical_key(val), []).append(val)

    case_map: dict[str, str] = {}
    case_merges: list[tuple[list[str], str]] = []
    for spellings in case_groups.values():
        if len(spellings) < 2:
            continue  # only one spelling — nothing to merge
        canonical = max(spellings, key=lambda v: (counts.get(v, 0), v))
        for s in spellings:
            if s != canonical:
                case_map[s] = canonical
        case_merges.append((spellings, canonical))

    suggestions = []

    # High-confidence: alias map clusters
    if alias_applied:
        by_canonical: dict[str, list[str]] = {}
        for orig, can in alias_applied.items():
            by_canonical.setdefault(can, []).append(orig)
        for can, origs in by_canonical.items():
            if len(origs) > 1 or origs[0] != can:
                suggestions.append({"original": origs, "canonical": can, "confidence": "high"})

    # High-confidence: case/whitespace merges
    for spellings, canonical in case_merges:
        suggestions.append({"original": spellings, "canonical": canonical, "confidence": "high"})

    # Medium-confidence: fuzzy clusters
    for group in clusters:
        # Pick most common as canonical label
        canonical = max(group, key=lambda v: counts.get(v, 0))
        suggestions.append({"original": group, "canonical": canonical, "confidence": "medium"})

    # Auto-apply high-confidence merges (alias map first, then case/whitespace)
    result = series.copy()
    alias_changed = 0
    alias_before: list[str] = []
    alias_after: list[str] = []
    case_changed = 0
    case_before: list[str] = []
    case_after: list[str] = []
    for i, val in series.items():
        if pd.isna(val):
            continue
        s = str(val)
        k = _canonical_key(s)
        if k in _ALIAS_MAP and _ALIAS_MAP[k] != s:
            alias_before.append(s)
            alias_after.append(_ALIAS_MAP[k])
            result.at[i] = _ALIAS_MAP[k]
            alias_changed += 1
        elif s in case_map:
            case_before.append(s)
            case_after.append(case_map[s])
            result.at[i] = case_map[s]
            case_changed += 1

    if alias_changed:
        ledger.add(ChangeRecord(
            table=table, column=column,
            rule="canonicalize_alias",
            cells_affected=alias_changed,
            before_sample=alias_before[:5], after_sample=alias_after[:5],
        ))
    if case_changed:
        ledger.add(ChangeRecord(
            table=table, column=column,
            rule="canonicalize_case",
            cells_affected=case_changed,
            before_sample=case_before[:5], after_sample=case_after[:5],
        ))

    return result, suggestions


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _is_strict_column(series: pd.Series) -> bool:
    """
    A "strict" column (numeric or date) distinguishes rows and must match exactly
    for two rows to be near-duplicates. These are exactly the columns a coarse
    text-key comparison ignores — quantity, price, timestamp — which is why
    transactional rows that merely share a few categoricals were over-flagged.
    """
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return True
    non_null = series.dropna().astype(str)
    if len(non_null) == 0:
        return False
    sample = non_null.head(50)
    iso_hits = sum(1 for v in sample if _ISO_DATE_RE.match(v.strip()))
    return iso_hits / len(sample) > 0.7


def _values_equal_strict(a: Any, b: Any) -> bool:
    a_na, b_na = pd.isna(a), pd.isna(b)
    if a_na or b_na:
        return bool(a_na and b_na)
    try:
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _rows_near_identical(
    a: pd.Series, b: pd.Series,
    strict_cols: list[str], text_cols: list[str], threshold: int,
) -> tuple[bool, float, list[str]]:
    """
    Two rows are near-duplicates only when they are near-identical across the
    *whole* row: every numeric/date column matches exactly, and every text column
    is equal or fuzzily-equal (punctuation/casing). At least one text column must
    differ textually — otherwise the rows are exact duplicates (reported separately).

    Returns (is_near, similarity, diff_fields) where diff_fields lists the text
    columns that actually differ — the "why" surfaced to the user.
    """
    for c in strict_cols:
        if not _values_equal_strict(a[c], b[c]):
            return False, 0.0, []

    scores: list[float] = []
    diff_fields: list[str] = []
    for c in text_cols:
        av, bv = a[c], b[c]
        a_na, b_na = pd.isna(av), pd.isna(bv)
        if a_na or b_na:
            if a_na and b_na:
                continue
            return False, 0.0, []  # one side null, the other not → distinct
        sa, sb = str(av).strip(), str(bv).strip()
        if sa == sb:
            continue
        score = fuzz.token_sort_ratio(sa, sb, processor=rf_utils.default_process)
        if score < threshold:
            return False, 0.0, []
        diff_fields.append(c)
        scores.append(score)

    if not diff_fields:
        return False, 0.0, []  # identical (exact dup) or no text difference → not "near"
    return True, min(scores) / 100.0, diff_fields


def find_near_duplicate_rows(
    df: pd.DataFrame,
    key_columns: list[str] | None = None,
    threshold: int = 85,
    max_rows: int = 5000,
) -> list[dict[str, Any]]:
    """
    Find near-duplicate rows: rows that are near-identical across the whole row,
    not merely matching on a handful of categorical columns. Numeric/date columns
    must match exactly; text columns may differ by punctuation/casing.

    Returns list of {indices: [i, j], similarity: float, sample: {...}}.
    Never removes rows.
    """
    if len(df) > max_rows:
        df = df.head(max_rows)
    if len(df) < 2:
        return []

    strict_cols = [c for c in df.columns if _is_strict_column(df[c])]
    text_cols = [c for c in df.columns if c not in strict_cols]

    # Cheap text "block" key prefilters candidate pairs so we don't compare every
    # O(n^2) pair cell-by-cell; the full per-column check below is what actually
    # decides a near-dup. Honour an explicit key_columns override for callers/tests.
    block_cols = [c for c in (key_columns or text_cols or list(df.columns)) if c in df.columns]

    def row_key(idx: int) -> str:
        return " | ".join(str(df.iloc[idx][c]) for c in block_cols)

    keys = [row_key(i) for i in range(len(df))]
    near_dupes = []
    checked: set[tuple[int, int]] = set()

    for i, k in enumerate(keys):
        # default_process lowercases + strips punctuation so "Acme, Inc." ~ "Acme Inc"
        # and "beta llc" ~ "Beta LLC" become candidates (exact-matching misses both).
        matches = process.extract(k, keys, scorer=fuzz.token_sort_ratio,
                                  processor=rf_utils.default_process,
                                  score_cutoff=threshold, limit=10)
        for _match_str, _score, j in matches:
            if i == j:
                continue
            pair = (min(i, j), max(i, j))
            if pair in checked:
                continue
            checked.add(pair)
            ok, sim, diff_fields = _rows_near_identical(
                df.iloc[i], df.iloc[j], strict_cols, text_cols, threshold
            )
            if ok:
                near_dupes.append({
                    "indices": list(pair),
                    "similarity": sim,
                    "diff_fields": diff_fields,
                    "sample": {
                        "a": dict(df.iloc[i]),
                        "b": dict(df.iloc[j]),
                    },
                })

    return near_dupes
