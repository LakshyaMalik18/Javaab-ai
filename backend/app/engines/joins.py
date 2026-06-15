"""
joins.py — ENGINE 2 (value-based join discovery — the headline feature).

Name matching alone fails on coded headers, so the score is dominated by
VALUE CONTAINMENT: do the foreign-key side's distinct values live inside the
primary-key side's distinct values? That survives gibberish column names
(fixture 06: two unrelated-looking `cst_id` columns must connect on values).

    discover_joins(tables, profiles) -> [ relationship dict, ... ]

Each relationship dict:
    { from_table, from_col,   # the FK / many side
      to_table,   to_col,     # the PK / one side
      confidence,             # 0..1 blended score (tests read this)
      confidence_label,       # high | medium | low
      name_sim, value_containment, cardinality_ok }
"""
from __future__ import annotations

import re
from itertools import combinations

import pandas as pd
from rapidfuzz import fuzz

# weights for the blended score — value containment dominates by design
_W_CONTAINMENT = 0.60
_W_CARDINALITY = 0.25
_W_TYPE = 0.10
_W_NAME = 0.05

# below this we don't surface a candidate at all
_MIN_SCORE = 0.55
_HIGH = 0.80
_MEDIUM = 0.65

# don't try to join on tiny/degenerate domains (e.g. booleans, single value)
_MIN_PK_DISTINCT = 2

_STRIP_SUFFIX = re.compile(r"(_?(id|key|code|no|num|fk|pk))+$", re.I)


def _strip_name(norm_name: str) -> str:
    base = _STRIP_SUFFIX.sub("", norm_name)
    return base or norm_name


def _name_similarity(a_norm: str, b_norm: str, pk_table: str) -> float:
    direct = fuzz.ratio(a_norm, b_norm) / 100.0
    stripped = fuzz.ratio(_strip_name(a_norm), _strip_name(b_norm)) / 100.0
    # convention: fk column "<table>_id" referencing table "<table>(s)"
    pk_singular = re.sub(r"s$", "", pk_table.lower())
    convention = 1.0 if _strip_name(a_norm) in (pk_table.lower(), pk_singular) else 0.0
    return max(direct, stripped, convention)


def _distinct_nonnull(series: pd.Series) -> set:
    vals = series.dropna()
    out = set()
    for v in vals:
        if isinstance(v, float) and v.is_integer():
            out.add(int(v))  # 1.0 and 1 should match across tables
        else:
            out.add(v)
    return out


def _type_compatible(p_from: dict, p_to: dict) -> bool:
    a, b = p_from["dtype"], p_to["dtype"]
    numeric = {"numeric"}
    if a in numeric and b in numeric:
        return True
    return a == b


def _evaluate_pair(
    t_from: str, c_from: str, df_from: pd.DataFrame, p_from: dict,
    t_to: str, c_to: str, df_to: pd.DataFrame, p_to: dict,
) -> dict | None:
    """Score `from.col` as an FK referencing `to.col` as a PK. None if implausible."""
    if not _type_compatible(p_from, p_to):
        return None

    pk_vals = _distinct_nonnull(df_to[c_to])
    fk_vals = _distinct_nonnull(df_from[c_from])
    if len(pk_vals) < _MIN_PK_DISTINCT or not fk_vals:
        return None

    # PK side must be (effectively) unique; FK side references it
    if not p_to.get("_unique"):
        return None

    containment = len(fk_vals & pk_vals) / len(fk_vals)
    if containment == 0.0:
        return None

    # a true FK usually repeats; a perfect 1:1 is still allowed but scores lower
    fk_repeats = len(fk_vals) < len(df_from[c_from].dropna())
    cardinality_ok = bool(p_to.get("_unique") and containment >= 0.5)

    name_sim = _name_similarity(p_from["norm_name"], p_to["norm_name"], t_to)
    type_score = 1.0

    score = (
        _W_CONTAINMENT * containment
        + _W_CARDINALITY * (1.0 if cardinality_ok else 0.0)
        + _W_TYPE * type_score
        + _W_NAME * name_sim
    )
    if fk_repeats:
        score = min(1.0, score + 0.02)  # nudge: looks like a real many-side

    if score < _MIN_SCORE:
        return None

    label = "high" if score >= _HIGH else "medium" if score >= _MEDIUM else "low"
    return {
        "from_table": t_from, "from_col": c_from,
        "to_table": t_to, "to_col": c_to,
        "confidence": round(score, 4),
        "confidence_label": label,
        "name_sim": round(name_sim, 4),
        "value_containment": round(containment, 4),
        "cardinality_ok": cardinality_ok,
    }


def discover_joins(
    tables: dict[str, pd.DataFrame],
    profiles: dict[str, dict],
) -> list[dict]:
    """Find ranked relationships across all table pairs by value containment."""
    relationships: list[dict] = []
    best_by_pair: dict[frozenset, dict] = {}

    for t1, t2 in combinations(tables.keys(), 2):
        df1, df2 = tables[t1], tables[t2]
        for c1 in df1.columns:
            p1 = profiles.get(t1, {}).get(c1)
            if p1 is None:
                continue
            for c2 in df2.columns:
                p2 = profiles.get(t2, {}).get(c2)
                if p2 is None:
                    continue
                # try both orientations (either side may be the PK)
                candidates = [
                    _evaluate_pair(t1, c1, df1, p1, t2, c2, df2, p2),
                    _evaluate_pair(t2, c2, df2, p2, t1, c1, df1, p1),
                ]
                for cand in candidates:
                    if cand is None:
                        continue
                    key = frozenset({(t1, c1), (t2, c2)})
                    prev = best_by_pair.get(key)
                    if prev is None or cand["confidence"] > prev["confidence"]:
                        best_by_pair[key] = cand

    relationships = list(best_by_pair.values())
    relationships.sort(key=lambda r: r["confidence"], reverse=True)

    # mark the FK columns on the profiles for the schema layer
    for rel in relationships:
        col_profile = profiles.get(rel["from_table"], {}).get(rel["from_col"])
        if col_profile is not None:
            col_profile["is_fk"] = True

    return relationships
