# Javaab — the analyst in your pocket

**Privacy-conscious natural-language analytics.** Upload messy CSV / Excel / JSON,
ask a question in plain English, and get back an insight, a chart, the data, and
the exact SQL that produced it.

Javaab is built on one non-negotiable principle: **the LLM is never the engine.**
The model writes a query or proposes a mapping — a real database (DuckDB) computes
the answer. The model never sees a raw cell and never invents a number. When it
isn't sure, Javaab refuses or asks a clarifying question instead of guessing. It is
Text-to-SQL with deterministic grounding, not retrieval-augmented chat over your
spreadsheet.

---

## Table of contents

1. [Overview](#1-overview)
2. [Core design philosophy](#2-core-design-philosophy)
3. [Architecture](#3-architecture)
4. [Features](#4-features)
   - [4.1 File ingestion](#41-file-ingestion)
   - [4.2 The cleaning engine](#42-the-cleaning-engine)
   - [4.3 Custom cleaning rules](#43-custom-cleaning-rules)
   - [4.4 Duplicate handling](#44-duplicate-handling)
   - [4.5 Schema understanding](#45-schema-understanding)
   - [4.6 Relationship / join discovery](#46-relationship--join-discovery)
   - [4.7 The four guards](#47-the-four-guards)
   - [4.8 Two-tier question resolution](#48-two-tier-question-resolution)
   - [4.9 Privacy & data handling](#49-privacy--data-handling)
   - [4.10 Provider fallback](#410-provider-fallback)
5. [Known limitations](#5-known-limitations)
6. [Roadmap (v2)](#6-roadmap-v2)
7. [Local development & setup](#7-local-development--setup)
8. [Testing](#8-testing)
9. [License](#9-license)

---

## 1. Overview

Javaab turns a messy data file into answerable questions. You upload one or more
CSV, Excel, or JSON files; Javaab cleans them deterministically, profiles every
column, asks a model to label what each column *means*, discovers how the tables
relate to one another by their values, and then lets you ask questions in plain
English.

A question flows through a fixed pipeline: it is mapped to real schema elements,
turned into a single read-only SQL query, validated by a static guardrail, executed
inside an in-memory DuckDB, and only then narrated. The answer you see leads with a
plain-English **insight**, followed by an interactive **chart**, the **result table**,
the **SQL that ran**, and a few suggested **follow-up questions**.

The defining property is **grounding**: every number on screen came out of DuckDB
running SQL you can read — not out of a language model's prose. If Javaab can't map
your question to the data, it tells you so and offers questions it *can* answer. It
fails loud, not quietly wrong.

---

## 2. Core design philosophy

Four principles run through the whole system. They are implemented as structure, not
as good intentions.

**1. Deterministic grounding — the engine computes, the model never invents numbers.**
Cleaning is pure Python; the LLM is never shown a raw cell. SQL is executed by DuckDB.
The model's job is to translate intent into a query or a structured mapping — the
arithmetic is the database's. A model cannot fabricate a total because it never
produces the total.

**2. Fail loud — refuse or clarify rather than guess.** The orchestrator has explicit,
deterministic gates: a question that maps to nothing in the schema is refused; a
business term that maps to two columns triggers a "which did you mean?"; a column the
question depends on that is still low-confidence (`provisional`) prompts a confirmation
before any SQL runs. A wrong-but-confident answer is treated as the worst outcome.

**3. Structural safety — make the critical rule impossible, not merely checked.**
Where a guarantee matters, it is enforced by construction:
- In **Privacy Mode** the session builds a *bare Groq provider* with no fallback
  wrapper. Gemini cannot be called because the object that would call it is never
  created — not because a flag is checked at call time.
- The join guardrail is passed only the **active link per table-pair**, so a query
  that joins on an alternative (inactive) key is rejected as an invalid join key.
  Your per-pair choice is load-bearing at query time.

**4. Transparency — every answer shows its work.** The generated SQL is returned with
every answer, assumptions the model made are surfaced explicitly, and the cleaning
engine records every transformation in a reversible change ledger. Nothing happens to
your data or your question that you can't inspect.

---

## 3. Architecture

### Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js (App Router) · React · TypeScript · Tailwind · Framer Motion · GSAP + Lenis (scroll) · Three.js (set-pieces) · Recharts (charts) — deployed on **Vercel** |
| **Backend** | FastAPI · Python 3.11+ · **DuckDB** (in-memory, per session) · **sqlglot** (SQL parsing/guarding) · **rapidfuzz** (fuzzy matching) · pandas · openpyxl — deployed on **Render** |
| **Models** | **Gemini 2.5 Flash-Lite** (default) · **Groq `llama-3.3-70b-versatile`** (Privacy Mode + automatic fallback) behind a single pluggable `LLMProvider` interface |

### Request flow

```
Upload ──► Cleaning ──► Schema ──► Relationships ──► Ask
  │           │            │             │             │
  CSV/XLSX/   deterministic LLM labels   value-based   NL → mapping → SQL →
  JSON →      clean + change  meaning +   join          guardrail → DuckDB →
  tables      ledger          confidence  discovery     insight + chart
```

Everything after upload lives in a single in-memory session. The cleaned tables, the
profiles, the schema contract, and the relationship graph are computed once and cached;
the per-session DuckDB connection is the only place data ever lives, and it is
`:memory:` — never a file on disk.

### Key endpoints

| Method & path | Purpose |
|---|---|
| `POST /session` | Create a session (optional `privacy_mode`, optional bring-your-own Gemini key) |
| `DELETE /session` | Explicit wipe — closes the in-memory DuckDB, drops all user data |
| `POST /upload` | Multi-file ingest → cleaning report + change ledger + duplicate/near-dup flags |
| `POST /apply-rules` | Add custom cleaning rules; re-runs cleaning from the retained raw data |
| `POST /remove-duplicates` | Remove user-selected duplicate rows (explicit action only) |
| `GET /schema` | The confidence-scored schema contract + relationship graph |
| `POST /confirm-schema` | Accept user edits / a pasted data dictionary |
| `POST /ask` | The full answer flow → insight, chart hint, table, SQL, follow-ups (or a clarify / refusal / block) |
| `GET /metrics` | Live trust-panel numbers (query metadata only — never user data) |

---

## 4. Features

Each feature below describes **what it does**, **how it works**, and **where it stops**.

### 4.1 File ingestion

**What it does.** Accepts CSV, Excel (`.xlsx`), and JSON, and turns each into one or
more clean tables. It detects encoding and BOM (UTF-8/16, with a latin-1 fallback),
sniffs the delimiter (`, ; \t |`), discovers the real header row by skipping
banner/preamble lines, flattens two-row merged headers, and drops trailing footer
rows like `Total` / `Source` / `Note`. Excel files yield one table per sheet; JSON
arrays-of-objects become tables with nested objects flattened to dotted column names
(`address.city`), and ragged records reconciled (missing keys → NULL).

**How it works.** `charset-normalizer` for encoding, Python's `csv.Sniffer` (with a
count-based fallback) for the delimiter, a header-likelihood scorer over the top rows,
and `openpyxl` for Excel. Excel is read twice — once for cached values
(`data_only=True`) and once for formulas — so a formula column imports its computed
result, and a formula cell whose result was *never cached* is detected and surfaced as
a warning rather than silently dropped.

**Limitations.**
- Excel **merged data cells are not filled down** — only the top-left cell carries the
  value.
- The old **`.xls`** binary format is not supported (`.xlsx` only).
- **JSON nested arrays are flattened to text**, not exploded into rows or columns
  (`["a","b"]` becomes the cell `"a, b"`); object/array elements are JSON-serialized
  to preserve structure as a string.
- An Excel **formula column with no cached value** can't be read — Javaab warns you to
  open and re-save the file in Excel so the results are computed, then re-upload.

### 4.2 The cleaning engine

**What it does.** Deterministically cleans every column and records what it changed.
It trims and collapses whitespace, unifies null tokens (`NA`, `N/A`, `-`, `--`, `null`,
`none`, `nil`, `nan`, `#N/A`, `?`, `unknown`, blank) to real NULLs, and coerces types
by **whole-column voting**: currency/units/thousands separators/percent/parenthesized
negatives → numbers (`$1,200` → `1200`, `(1,234)` → `-1234`, `10%` → `0.10`,
`1.2M` → `1200000`); dates → ISO `YYYY-MM-DD`; yes/no/y/n/t/f/1/0 → booleans. It then
canonicalizes categorical values (see below) and detects exact and near-duplicate rows.

**How it works.** Type is inferred per column from the share of values that parse, so a
single stray cell doesn't flip the column. Date order (DD/MM vs MM/DD) is resolved by
scanning the whole column: if **any** value has a first field > 12 the order is fixed
for the column; if nothing disambiguates it, the column is **flagged as ambiguous**
rather than silently guessed. Every transformation is appended to a **ChangeLedger**
(`table, column, rule, cells_affected, before/after sample`) that drives the "what I
changed" report.

**Limitations.**
- Genuinely ambiguous date columns and columns with mixed residual types are **flagged,
  not auto-resolved** — by design.
- Cleaning is column-level; there are no row-level or conditional rules yet (see §4.3).

### 4.3 Custom cleaning rules

**What it does.** After seeing the cleaning report you can add rules that re-run the
engine. Three rule types are supported:
1. **Add a null token** — treat an extra value (e.g. `n.a.`) as NULL.
2. **Force a column's type** — pin a column to numeric / date / boolean / text and stop
   the engine second-guessing it.
3. **Merge category values** — collapse a set of values into one canonical label.

**How it works.** Rules are validated, accumulated for the session, and the **full** set
is re-applied from the **retained raw data** on every run — so the result is the same
regardless of the order you added them. The raw, pre-clean frames are kept in memory
exactly so this re-run needs no re-upload.

**Limitations.**
- No **regex / literal find-and-replace**, no **explicit date-format** rule, and no
  **exclude-column** rule yet (all on the roadmap).
- The null-token rule is currently table-wide, not per-column.
- Because rules re-run cleaning from raw, any rows you had previously removed as
  duplicates **reappear** and are re-flagged (see §4.4).

### 4.4 Duplicate handling

**What it does.** Surfaces two kinds of duplicates: **exact** (every cell identical) and
**near** (`"Acme Inc"` vs `"Acme, Inc."` — same row apart from punctuation/casing in
text fields, with all numeric/date fields identical). Both are reported with samples and
the specific differing fields, and **nothing is ever auto-deleted**.

**How it works.** Exact duplicates are found by hashing whole rows. Near-duplicates use
`rapidfuzz` token-sort similarity, but only after a strict gate: every numeric and date
column must match *exactly*, and at least one text column must differ — so transactional
rows that merely share a few categoricals are not over-flagged. Removal happens **only**
on explicit user action (`POST /remove-duplicates`), and even then the first row of each
group is kept as the representative.

**Limitations.**
- Removed duplicates are an edit on the *cleaned* set, not a cleaning rule — so if you
  later apply a custom rule (which rebuilds from raw), the removed rows return and are
  re-flagged.

### 4.5 Schema understanding

**What it does.** Gives every column a plain-English meaning, a business role
(`id / dimension / measure / timestamp / text`), an inferred type, and a **confidence
score**. Low-confidence columns are flagged with the model's own clarifying question.
You can edit any field, and you can paste a **data dictionary** (column → description)
whose statements override the AI's guesses (and lock the confidence high).

**How it works.** A deterministic profiler computes the hard facts per column
(dtype, null %, distinct count, cardinality ratio, sample values, min/max, a pattern
fingerprint for emails/UUIDs/currency/etc., and a likely role). The model is then asked,
**once per session**, to label *meaning* from that profile — it never sees raw cells.
The result is a cached **schema contract** that is the single source of truth for SQL
generation. If no model key is configured, a usable deterministic contract is built
instead (with lower confidences).

**Limitations.**
- The data dictionary is accepted as **pasted / structured entries**; there is no bulk
  dictionary *file* upload yet.

### 4.6 Relationship / join discovery

**What it does.** Finds how tables relate **by their values**, not just their names — so
it connects `cst_id` to `id` even when the headers are coded gibberish. It produces a
ranked relationship graph (direction + cardinality), auto-links high-confidence edges
for a glance-confirm, proposes medium-confidence ones, and lets you confirm, edit, add,
or override links manually. Multi-hop paths (A→B→C) are traversed so a question can span
tables that aren't directly joined.

**How it works.** Each candidate table-pair/column-pair is scored on a weighted blend
dominated by **value containment** (do the FK side's distinct values live inside the PK
side's?), plus cardinality/key fit, type compatibility, and a small name-similarity
term. Two classes of **coincidental overlap are actively suppressed**: a small measure
column (e.g. a count) that happens to fall inside an `1..N` id range, and a text/dimension
column whose small value set happens to sit inside an unrelated key — both are penalised
unless a real name match rescues them. Only one **active** link is kept per table-pair,
and that active set is what the query guardrail enforces.

**Limitations.**
- Reliability on **opaque/coded keys degrades past ~4 tables** — beyond that Javaab
  prefers to refuse rather than answer on a shaky join path (see §5).
- Manually-defined joins are **not preserved across a same-session rebuild** (re-upload
  or applying a cleaning rule warns you and asks you to redefine them — see §5).

### 4.7 The four guards

Before any SQL touches DuckDB, it passes a static guardrail built on `sqlglot`. Four
distinct guards run, each with its own honest failure message:

1. **Read-only / destructive block.** Exactly one statement, and it must be a `SELECT`
   (or `WITH … SELECT`). Any `DELETE / DROP / UPDATE / INSERT / ALTER / CREATE / COPY /
   PRAGMA / ATTACH / …` anywhere is blocked — and destructive *intent* phrased in plain
   English ("delete all orders") is caught before SQL is even generated.
2. **Cross-join / cartesian block.** A comma-join or explicit `CROSS JOIN` with no `ON`
   condition multiplies rows and can return a confident wrong number — it is refused.
3. **Completeness guard.** The query must join every table on the resolved join path; a
   query that silently drops a required table (turning "enterprise revenue" into "all
   revenue") is refused rather than answered partially.
4. **Invalid-join-key guard.** Every inter-table join must weld the tables on a
   *discovered* FK→PK key. A join that uses real tables and real columns but the **wrong
   key** (which would silently return zero or wrong rows) is rejected.

Every decision — allowed or blocked, with its reason — is logged to an in-memory metrics
store containing **query metadata only, never user data**, which feeds the live trust
panel.

**Limitation.** The completeness guard applies to the **deterministic Tier-1 path only**;
the Tier-2 path owns its own validation and does not carry a required-table set.

### 4.8 Two-tier question resolution

**What it does.** Answers both precise and vague questions without ever guessing silently.

- **Tier 1 (deterministic).** A literal mapping matches your question to columns by name
  and business-synonym overlap. On a clean hit it generates SQL directly. If a synonym
  maps to two columns, or a needed column is still low-confidence, it stops and asks.
- **Tier 2 (LLM-proposed mapping, re-validated).** When Tier 1 matches nothing (e.g.
  "how many bond1 sold", where neither token is a column name), the model is asked for a
  **structured mapping** — which tables/columns, which value-level filters, which
  aggregation, and a confidence signal. It returns a *mapping, never SQL and never a
  number*. The engine then **re-validates every table, column, and filter value against
  the real schema and data** before generating any SQL: a hallucinated column or a
  value that doesn't exist is rejected and never executed. A low-confidence or ambiguous
  proposal becomes a clarifying question instead of a silent pick.

When Javaab asks a clarifying question, it attaches its single best-guess restatement so
the UI can offer a **"Yes — run it"** shortcut; affirming simply re-asks that concrete,
unambiguous question on a fresh, stateless request.

**Limitations.** Tier 2 depends on a model call, so it is subject to rate limits and the
fallback in §4.10. It maps *values* it can find in your data — it does not invent
semantic equivalences (see §5 on abbreviations).

### 4.9 Privacy & data handling

**What it does.** By default, your data is **ephemeral and in-memory only**. Uploaded
files become DataFrames registered in a per-session DuckDB connection opened on
`:memory:` — never a file path. On explicit session end, idle timeout, or process
shutdown, `wipe()` runs deterministically: tables are unregistered, the DuckDB
connection is closed, and every user-derived object (including the raw frames and any
supplied key) is dropped. **Nothing user-derived is ever written to disk.**

**Privacy Mode** is a stronger, opt-in posture: the session is built on a **bare Groq
provider** (`llama-3.3-70b-versatile`, a no-retention model) with **no fallback wrapper**,
so your data is *structurally incapable* of being routed to Gemini — the object that
would call Gemini is never constructed.

**Bring-your-own Gemini key** is supported for the default mode; the key is held in
memory for the session only, never persisted and never logged.

**How it works.** The only metadata that outlives a session is the guardrail metrics
(allowed/blocked counts, block reasons, table names) — query metadata, never cell
values.

**Limitation.** In default mode, your question and small data samples are sent to the
model provider to generate SQL and insights — Javaab is privacy-*conscious*, not fully
local. Groq's policy is no-retention; Google's free Gemini tier may retain prompts.
Privacy Mode and the no-retention path exist precisely for when that matters.

### 4.10 Provider fallback

**What it does.** In default mode, if Gemini is *temporarily* unavailable — rate-limited
(429), overloaded (5xx), or it timed out — the identical call is transparently retried on
Groq so you still get an answer. The answer then carries a small, honest note that the
fallback model produced it.

**How it works.** A `FallbackProvider` wraps the primary. Only **transient availability**
failures (429 / 503 / timeout / network) trigger the retry. A failure that means the
*request or config is wrong* — a 4xx auth error, a missing key — is **not** silently
rerouted; it surfaces loudly so a real misconfiguration is visible. The fallback always
uses the server's Groq key, never your Gemini key. A read-only **provider indicator**
shows which model is active.

**Limitation.** Fallback exists only in default mode. In Privacy Mode there is no
fallback by design — if Groq is busy, the request fails rather than route your data
elsewhere.

---

## 5. Known limitations

An honest, consolidated list of where v1 stops:

- **Completeness guard is Tier-1 only.** The Tier-2 (LLM-mapping) path validates itself
  but does not carry the deterministic required-table set.
- **No abbreviation / synonym mapping.** Javaab maps to values that exist in your data;
  it will **not** assume `NY` means `New York` or that two spellings are semantically
  equal. This is deliberate — guessing equivalence is how tools silently group data
  wrong. (Exact/near-duplicate canonicalization within a column is handled; cross-value
  *meaning* is not.)
- **The insight sentence is LLM-generated prose.** The table and chart numbers are exact
  (they came out of DuckDB), but the one-line narration around them is not independently
  verified. Trust the numbers and the SQL; read the prose as a summary.
- **Manual joins aren't preserved across a same-session rebuild.** Re-uploading or
  applying a cleaning rule rebuilds the schema contract and discards manually-defined
  joins — Javaab *warns* you and asks you to redefine them, but does not yet persist
  them (v1.1).
- **Removed duplicates reappear if cleaning re-runs.** Duplicate removal edits the cleaned
  set; applying a custom rule rebuilds from raw, so removed rows return and are
  re-flagged.
- **Multi-table join reliability degrades past ~4 tables on opaque/coded keys.** Rather
  than answer on a shaky join path, Javaab refuses.
- **No usage analytics or accuracy-evaluation harness yet.** The trust panel reports real
  session/guardrail metadata only; there is no automated accuracy benchmark in v1.

---

## 6. Roadmap (v2)

- **Richer currency parsing** (more symbols, locale-aware formats).
- **More data sources** — SQL Server, Snowflake, Databricks connectors.
- **Advanced cleaning rules** — regex find/replace, explicit date-format, exclude-column.
- **Bulk data-dictionary upload** (a file, not just pasted entries).
- **Abbreviation / synonym mapping** (opt-in, so `NY` ↔ `New York` becomes possible).
- **Preserve removed rows across a re-clean.**
- **Persist manual joins across a rebuild.**
- **JSON nested arrays → real queryable columns** (explode instead of stringify).
- **An accuracy evaluation harness** with a graded question corpus.
- **Row-level / conditional cleaning rules.**

---

## 7. Local development & setup

**Prerequisites:** Python **3.11+** and Node.js (for the frontend).

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Environment variables

| Variable | Used by | Notes |
|---|---|---|
| `GEMINI_API_KEY` | Backend (default model) | Server key for Gemini 2.5 Flash-Lite. Users can also bring their own key per session. |
| `GROQ_API_KEY` | Backend (Privacy Mode + fallback) | Server key for Groq `llama-3.3-70b-versatile`. |
| `JAVAAB_CORS_ORIGINS` | Backend | Comma-separated allowed origins. Unset → permissive (`*`) for local dev; lock to your frontend domain in production. |

Without any model key the backend still runs and builds a deterministic schema contract,
but question-answering that needs the model (Tier 2, insight narration) will be limited.

---

## 8. Testing

The backend ships with a comprehensive suite — **203 tests** total (**199 run by
default**; 4 are marked `live` and hit a real model, so they're excluded from the normal
run). They cover the full engine surface:

- **Ingestion** — encoding/BOM, delimiter sniffing, header discovery, multi-row headers,
  footer dropping, multi-sheet Excel, formula handling, nested JSON.
- **Cleaning** — null unification, currency/percent/date coercion, whole-column date
  voting and ambiguity flagging, the change ledger.
- **Canonicalization** — alias merges, case/whitespace collapsing, exact and near
  duplicates.
- **Profiler** — role inference, pattern fingerprints, cardinality.
- **Joins** — value-containment scoring, coincidental-overlap suppression (numeric and
  text), multi-hop traversal.
- **Guardrail** — destructive blocks, cross-join, completeness, invalid-join-key.
- **Provider fallback** — transient-vs-config error routing.
- **API (Phase 4)** — session lifecycle, upload, schema confirm, ask, metrics.

Run them from `backend/`:

```bash
pytest                 # default run (excludes live model tests)
pytest -m live         # the live tests (require real model keys)
pytest -m ''           # everything, including live
```

---

## 9. License

Copyright © 2026 Lakshya Malik. All rights reserved.

Javaab is **source-available, not open source**. It is published here for portfolio and
demonstration purposes — you're welcome to read the code, learn from it, and run it for
your own noncommercial use.

It is licensed under the [PolyForm Noncommercial License 1.0.0](./LICENSE.md). In short:
personal, educational, and other noncommercial use is permitted; **commercial use is
not.** See [`LICENSE.md`](./LICENSE.md) for the full terms.
