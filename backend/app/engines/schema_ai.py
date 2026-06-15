"""
schema_ai.py — §4 LLM semantic layer (ONE call per file, cached per session).

Input: the deterministic per-column profile (from profiler.py).
Output: per-column plain-English meaning + a confidence 0..1, plus a one-line
table summary. The model is explicitly told to FLAG low-confidence columns with a
clarifying question instead of hallucinating a meaning — this is what makes the
contract fail loud rather than guess.

The call is isolated here so it can be mocked deterministically in tests and
swapped for any LLMProvider.
"""
from __future__ import annotations

import json

from app.llm.base import LLMError, LLMProvider

# A stable role marker so a test mock (and a human) can tell which engine called.
SYSTEM_TAG = "ROLE: javaab-schema-labeler"

_SYSTEM = f"""{SYSTEM_TAG}
You are a data analyst labelling the columns of database tables for a SQL engine.
For every column you are given deterministic stats (name, type, role, sample
values). Return STRICT JSON only.

Rules:
- Give each column a short plain-English `meaning` (<= 12 words).
- Give a `confidence` from 0.0 to 1.0 for how sure you are of that meaning.
- If a column is genuinely ambiguous (e.g. a coded name with values that could
  mean two different things), set confidence <= 0.5 and put a concrete
  `clarifying_question` for the user. NEVER invent a confident meaning you are
  unsure of — flag it instead.
- Also give each table a one-line `summary`.

Output JSON shape:
{{
  "tables": {{
    "<table_name>": {{
      "summary": "<one line>",
      "columns": {{
        "<column_name>": {{
          "meaning": "<plain english>",
          "confidence": <0..1>,
          "clarifying_question": "<question or null>"
        }}
      }}
    }}
  }}
}}
"""


def _profile_digest(profiles: dict[str, dict]) -> dict:
    """Trim the deterministic profile to just what the model needs (token-lean)."""
    out: dict = {}
    for table, cols in profiles.items():
        out[table] = {}
        for col, p in cols.items():
            out[table][col] = {
                "type": p.get("dtype"),
                "role": p.get("role"),
                "is_id": p.get("is_id", False),
                "is_fk": p.get("is_fk", False),
                "distinct": p.get("distinct_count"),
                "null_pct": p.get("null_pct"),
                "samples": [str(v) for v in p.get("sample_values", [])[:5]],
            }
    return out


def label_schema(
    profiles: dict[str, dict],
    provider: LLMProvider,
    *,
    max_tokens: int = 2048,
) -> dict:
    """Call the LLM once for the whole upload. Returns the parsed `tables` map.

    Never raises on a model hiccup — returns {} so the contract falls back to
    deterministic heuristics (still usable, just lower confidence)."""
    digest = _profile_digest(profiles)
    user = (
        "Label these tables/columns. Profiles:\n"
        + json.dumps(digest, default=str)
    )
    try:
        result = provider.complete_json(_SYSTEM, user, max_tokens=max_tokens)
    except LLMError:
        return {}
    tables = result.get("tables")
    return tables if isinstance(tables, dict) else {}
