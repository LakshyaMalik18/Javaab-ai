# Decisions

A running log of consequential engineering decisions and their rationale.

## 2026-06-15 — Pinned pandas to the 2.2–2.3 family

**Decision:** Changed `pandas>=2.2.0` to `pandas>=2.2.0,<2.4` in `backend/pyproject.toml`.

**Why:** The unbounded version range let Render install a stricter pandas than the
version local development had been passing tests against. Newer pandas forbids
assigning a float into a cell of a string-dtype Series, which surfaced a latent bug
in `_coerce_numeric_column` (`cleaning.py`) that mutated a string-dtype Series
cell-by-cell. Local tests were all green on the older pandas, so the bug only showed
up on the deployed environment — a classic "the environment you test in must match
the environment you ship in" failure.

**Fix paired with the pin:** `_coerce_numeric_column` now accumulates coerced values
into a plain list (seeded from the originals) and assembles a fresh `pd.Series` in one
go, instead of mutating a string-dtype Series in place. Cleaning behavior and the
ground-truth numbers are unchanged (full test suite green on pandas 2.3.3).

**Follow-up (optional):** Pin the Python version on Render to match local so this
class of drift is fully closed.
