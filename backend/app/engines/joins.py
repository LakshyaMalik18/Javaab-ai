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
from numbers import Integral

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

# --- coincidental-overlap suppression (the "number-column" false positive) ---
# A measure column (frequency, months-since-coupon, qty…) is a small domain of
# integers that, by sheer luck, lives inside a contiguous id range like 1..N and
# so "contains" perfectly. That overlap is meaningless. We PENALISE (not delete)
# such a candidate so a coincidence sinks below threshold, while a genuine numeric
# key with a matching name is rescued by name similarity and merely demoted.
_MEASURE_FK_PENALTY = 0.50      # max points shaved off a coincidental measure FK
_NAME_RESCUE_FULL = 0.70        # name_sim ≥ this → no penalty (real key, keep it)
_NAME_RESCUE_START = 0.50       # name_sim ≤ this → full penalty
_COINCIDENCE_MAX_DISTINCT = 50  # "small" integer domain on the FK side
_CONTIGUOUS_RATIO = 0.90        # PK ids fill ≥90% of their min..max span

# A text/dimension column (desk, status, region…) can ALSO coincidentally have its
# small set of values sit inside some unrelated PK's value set — same false positive
# as the number case, just non-numeric. Suppress it identically: graded penalty,
# rescued by name similarity, and never applied to an id-shaped column (a coded key
# like cst_id is a legitimate text/categorical key and must survive).
_TEXT_FK_PENALTY = 0.50         # max points shaved off a coincidental text/dim FK
_TEXT_MIN_CONTAINMENT = 0.50    # the FK values mostly live inside the PK's set

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


def _all_int(vals: set) -> bool:
    # accept python ints and numpy integer types (pandas yields np.int64); _bool_
    # is Integral too, so exclude it explicitly
    return bool(vals) and all(
        isinstance(v, Integral) and not isinstance(v, bool) for v in vals
    )


def _is_coincidental_overlap(
    p_from: dict, fk_vals: set, p_to: dict, pk_vals: set, fk_repeats: bool
) -> bool:
    """The coincidence signature: a *measure* FK whose small distinct integer
    domain happens to fall inside a contiguous id range on the PK side."""
    if (p_from.get("role") or p_from.get("likely_role")) != "measure":
        return False
    if not fk_repeats:                       # a 1:1 column is not this kind of noise
        return False
    if not _all_int(fk_vals) or len(fk_vals) > _COINCIDENCE_MAX_DISTINCT:
        return False
    # PK side is an "id range" structurally: a unique, dense, contiguous run of
    # integers (1..N). Coded names like `eventid` don't always profile as role=id,
    # so we detect the shape, not the label. (_unique is guaranteed by the caller.)
    if not _all_int(pk_vals):
        return False
    lo, hi = min(pk_vals), max(pk_vals)
    span = hi - lo + 1
    if span <= 0 or len(pk_vals) / span < _CONTIGUOUS_RATIO:
        return False
    # the small measure domain sits entirely inside that id range — the coincidence
    return all(lo <= v <= hi for v in fk_vals)


def _id_shaped_name(norm_name: str) -> bool:
    """True if the name carries an id-ish suffix (id/key/code/no/num/fk/pk) — i.e.
    stripping it changes the name. Such columns are legitimate (coded) keys and are
    exempt from coincidental-overlap suppression."""
    return _strip_name(norm_name) != norm_name


def _is_coincidental_text_overlap(
    p_from: dict, fk_vals: set, p_to: dict, fk_repeats: bool, containment: float
) -> bool:
    """The non-numeric coincidence: a *dimension/text* FK (not id-shaped) whose
    small value set happens to be contained in an unrelated PK's values, with no
    naming relationship to corroborate it. Name rescue is applied via the penalty."""
    if (p_from.get("role") or p_from.get("likely_role")) not in ("dimension", "text"):
        return False
    if p_from.get("is_id") or _id_shaped_name(p_from.get("norm_name", "")):
        return False                          # a coded key, not coincidental noise
    if not fk_repeats:
        return False
    if not fk_vals or len(fk_vals) > _COINCIDENCE_MAX_DISTINCT:
        return False
    return containment >= _TEXT_MIN_CONTAINMENT


def _coincidence_penalty(name_sim: float, cap: float = _MEASURE_FK_PENALTY) -> float:
    """Graded penalty: full when the names don't match, fading to zero as name_sim
    rises into the rescue band (so a real, well-named key is kept). `cap` is the max
    penalty for the kind of overlap (measure vs text)."""
    if name_sim >= _NAME_RESCUE_FULL:
        return 0.0
    if name_sim <= _NAME_RESCUE_START:
        return cap
    span = _NAME_RESCUE_FULL - _NAME_RESCUE_START
    severity = (_NAME_RESCUE_FULL - name_sim) / span
    return cap * severity


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

    # suppress coincidental overlaps (graded, name-rescued). Two flavours, mutually
    # exclusive by FK role: a measure dropping into an id range, or a text/dimension
    # column whose small value set happens to sit inside an unrelated PK's values.
    if _is_coincidental_overlap(p_from, fk_vals, p_to, pk_vals, fk_repeats):
        score = max(0.0, score - _coincidence_penalty(name_sim, _MEASURE_FK_PENALTY))
    elif _is_coincidental_text_overlap(p_from, fk_vals, p_to, fk_repeats, containment):
        score = max(0.0, score - _coincidence_penalty(name_sim, _TEXT_FK_PENALTY))

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
