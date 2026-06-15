# Javaab — INDEPENDENT AUDIT test suite

This is a **separate, outside-the-agent audit** of the Phase 1 engines. It is NOT
meant to replace Claude Code's own tests — keep both. Everything here is suffixed
`_audit` so nothing collides with files Claude Code already created.

Contents:
- `fixtures_audit/` — 12 hand-built edge-case datasets (CSV / multi-sheet XLSX / JSON / BOM+Latin-1 / empty / garbage).
- `_harness.py` — the ONE adapter the audit tests call. They assert on behaviour, not module layout.
- `test_ingest_audit.py`, `test_cleaning_audit.py`, `test_canonical_audit.py` — Phase 1.
- `test_profiler_audit.py`, `test_joins_audit.py` — later phases (marked `profiler` / `joins`).
- `FIXTURES_SPEC.md` — the spec each fixture + assertion was built from.
- `_make_large.py` — builds a 100k-row CSV at test time (sampling-path test).
- `fixtures_audit/_generate.py` — regenerates all fixture files deterministically.

## Why this exists
"All my tests pass" written by the same agent that wrote the engine is a weak
signal — it tends to test what was built, not what the spec demanded. This suite
is an independent check of the hard cases: coded-header joins, USA/America
collapse, fuzzy near-dups, and graceful empty/garbage handling.

## Wiring (do this once)
The audit tests start RED with `NotImplementedError: Harness not wired yet`.
That's intended. To run them:
1. Open `_harness.py`, implement `_run_real(...)` so it calls the EXISTING
   ingest/cleaning/canonical engines and returns a populated `CleanResult`.
2. Set `HARNESS_WIRED = True`.
3. **Do not modify the test files or fixtures.** Fix the engine if a test fails —
   unless an assertion encodes a genuinely arbitrary choice (e.g. percent as
   0.10 vs 10.0), in which case flag it for a human decision.

## Running ONLY the audit suite (won't touch Claude Code's own tests)
```bash
# Phase 1 audit (skips profiler/joins):
pytest tests/test_ingest_audit.py tests/test_cleaning_audit.py tests/test_canonical_audit.py

# or by marker, audit files only:
pytest tests/test_*_audit.py -m "not joins and not profiler"

# regenerate audit fixtures if needed:
python tests/fixtures_audit/_generate.py
```

## Audit pass bar
Phase 1 is genuinely done when the audit tests for ingest + cleaning + canonical
pass — INCLUDING fixtures 06 (coded-header values), 08 (USA/America -> 500),
09 (fuzzy near-dups), and 11/12 (empty / garbage / weird encoding).
