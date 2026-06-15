"""Canonicalization + near-duplicate detection using rapidfuzz."""
from __future__ import annotations

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


def find_near_duplicate_rows(
    df: pd.DataFrame,
    key_columns: list[str] | None = None,
    threshold: int = 85,
    max_rows: int = 5000,
) -> list[dict[str, Any]]:
    """
    Find near-duplicate rows using fuzzy matching on key text columns.
    Returns list of {indices: [i, j], similarity: float, sample: {...}}.
    Never removes rows.
    """
    if key_columns is None:
        key_columns = [c for c in df.columns if df[c].dtype == object][:3]

    if len(df) > max_rows:
        df = df.head(max_rows)

    def row_key(row: pd.Series) -> str:
        return " | ".join(str(row[c]) for c in key_columns if c in row.index)

    keys = [row_key(df.iloc[i]) for i in range(len(df))]
    near_dupes = []
    checked: set[tuple[int, int]] = set()

    for i, k in enumerate(keys):
        # default_process lowercases + strips punctuation so "Acme, Inc." ~ "Acme Inc"
        # and "beta llc" ~ "Beta LLC" are caught (exact-matching would miss both).
        matches = process.extract(k, keys, scorer=fuzz.token_sort_ratio,
                                  processor=rf_utils.default_process,
                                  score_cutoff=threshold, limit=10)
        for match_str, score, j in matches:
            if i == j:
                continue
            pair = (min(i, j), max(i, j))
            if pair in checked:
                continue
            checked.add(pair)
            near_dupes.append({
                "indices": list(pair),
                "similarity": score / 100.0,
                "sample": {
                    "a": dict(df.iloc[i]),
                    "b": dict(df.iloc[j]),
                },
            })

    return near_dupes
