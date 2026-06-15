# Javaab — Edge-Case Test Fixture Spec (Phase 1 corpus)

Generate **all 12** fixtures up front. Implement engines **easiest → hardest** against them. Files are tiny and synthetic — no real data.

**Location:** `backend/tests/fixtures/<NN>_<name>/`
**Tests:** `backend/tests/test_<engine>.py` (pytest). Each test loads a fixture, runs the relevant engine, and inspects the **ChangeLedger** and/or the resulting in-session DuckDB tables.

**Engine tags** (which engine an assertion belongs to — gates when it must pass):
`INGEST` · `CLEANING` · `CANONICAL` · `PROFILER` · `JOINS`
Phase 1 (cleaning) must pass all `INGEST` + `CLEANING` + `CANONICAL` assertions. `PROFILER`/`JOINS` assertions activate when those engines are built (§5 of the build plan). **The 20-question NL→SQL accuracy test is NOT here** — it lives with the query layer, later.

---

## 01 — `01_clean_basic/data.csv`  · Tier: trivial
Baseline. Proves a clean file passes through untouched.
```csv
order_id,product,quantity,price
1,Widget,3,9.99
2,Gadget,1,19.99
3,Widget,5,9.99
```
**Assertions**
- `INGEST`: 1 table, 3 rows, 4 columns; comma delimiter; UTF-8.
- `CLEANING`: types = int, text, int, float. ChangeLedger is **empty** (nothing to fix). Zero nulls.

---

## 02 — `02_join_pair/` (`customers.csv`, `orders.csv`)  · Tier: easy
Clean two-file set; auto-join must fire.
```csv
# customers.csv
id,name,segment
1,Alice,Enterprise
2,Bob,SMB
3,Carol,Enterprise
```
```csv
# orders.csv
order_id,customer_id,amount
100,1,250.00
101,1,75.00
102,2,500.00
103,3,125.00
```
**Assertions**
- `INGEST`/`CLEANING`: 2 clean tables, correct types.
- `PROFILER`: `customers.id` flagged id-like (unique, non-null); `orders.customer_id` flagged FK-like (repeats).
- `JOINS`: proposes `orders.customer_id → customers.id`, **high confidence** — value containment {1,2,3} ⊆ {1,2,3}, name match, cardinality (PK unique / FK many). One edge in the relationship graph.

---

## 03 — `03_money_formats/data.csv`  · Tier: medium
Currency, parentheses-negatives, percent, thousands separators.
```csv
item,revenue,discount,units
A,"$1,234.50",10%,"1,200"
B,"$987.00",5%,"950"
C,"($45.00)",0%,"3,400"
```
**Assertions**
- `CLEANING`: `revenue` → float `[1234.50, 987.00, -45.00]` (strip `$`/commas; `()` → negative). `discount` → fraction `[0.10, 0.05, 0.00]` (define: `%` → fraction). `units` → int `[1200, 950, 3400]`.
- ChangeLedger records: currency-strip, thousands-sep, paren-negative, percent-convert — each with cells affected + before/after sample. All reversible.

---

## 04 — `04_ambiguous_dates/data.csv`  · Tier: medium
Whole-column format voting + the ambiguous-flag path.
```csv
event,date,date2
A,03/04/2026,01/02/2026
B,15/04/2026,03/02/2026
C,07/04/2026,05/02/2026
```
**Assertions**
- `CLEANING`: `date` — a value has day field `15` (>12), so the **whole column** resolves to **DD/MM** → ISO `[2026-04-03, 2026-04-15, 2026-04-07]`. Column **not** flagged (vote resolved).
- `date2` — every field ≤ 12, genuinely ambiguous → **flagged**, best-guess applied (locale default) but marked `provisional` for the schema-confirm step. No silent confident guess.

---

## 05 — `05_messy_nulls/data.csv`  · Tier: medium
Many null spellings in one column.
```csv
id,status,notes
1,active,ok
2,NA,
3,-,n/a
4,inactive,N/A
5,,none
```
**Assertions**
- `CLEANING`: null-token unification → `NA, -, "", N/A, n/a, none` become real NULL. `status` ends with 2 distinct non-null values (`active`, `inactive`). `notes` ends mostly NULL.
- ChangeLedger logs the null-token rule + per-column null %. User-extensible: adding a token re-runs and updates the ledger.

---

## 06 — `06_coded_headers/` (`cstm.csv`, `ordr.csv`)  · Tier: hard
Gibberish headers — schema + join must work via **values**, not names.
```csv
# cstm.csv
cst_id,cst_nm,sgmt
1,Alice,ENT
2,Bob,SMB
3,Carol,ENT
```
```csv
# ordr.csv
ord_id,cst_id,amt,ord_dt
100,1,250,03/04/2026
101,2,500,15/04/2026
102,1,125,07/04/2026
```
**Assertions**
- `CLEANING`: `amt` → numeric; `ord_dt` → ISO (DD/MM by voting).
- `PROFILER`: `cst_id` profiled as id-like in `cstm`, FK-like in `ordr` (deterministic — no LLM needed for this assertion).
- `JOINS`: links `ordr.cst_id → cstm.cst_id` via value containment **even though headers are coded**. This is the moat fixture.

---

## 07 — `07_preamble_header/data.csv`  · Tier: hard
Banner/preamble rows + a 2-row header to flatten.
```csv
ACME Corp - Sales Export
(do not distribute)

Region,2026,2026
Region,Revenue,Units
North,12000,300
South,9500,210
West,8000,150
```
**Assertions**
- `INGEST`: skip the 3 preamble/blank lines (banner + blank); header-likelihood scoring finds the real header starting at the `Region,2026,2026` row. Detect the **2-row header** and flatten → `["Region", "2026 Revenue", "2026 Units"]` (dedupe the doubled `Region`). Result: 3 data rows, 3 columns.
- `CLEANING`: `2026 Revenue`, `2026 Units` → int.

---

## 08 — `08_canonicalize/data.csv`  · Tier: hard
The GROUP-BY-breaking case.
```csv
customer,country,sales
A,USA,100
B,U.S.A.,200
C,America,150
D,United States,50
E,Canada,80
```
**Assertions**
- `CANONICAL`: cluster `{USA, U.S.A., America, United States}` → one canonical label (alias map + fuzzy). Apply (high confidence), log a 4-way merge in the ledger, **reversible**, label editable. `Canada` untouched.
- Post-canonicalization, a `GROUP BY country` yields exactly 2 groups: canonical-US (sum **500**) and Canada (**80**). This is the assertion that proves the feature.

---

## 09 — `09_near_dupes/data.csv`  · Tier: hard
Fuzzy duplicates that exact-matching misses + one true exact dup.
```csv
company,city,amount
Acme Inc,NYC,100
"Acme, Inc.",NYC,100
Beta LLC,LA,200
beta llc,LA,200
Gamma Co,SF,300
Gamma Co,SF,300
```
**Assertions**
- `CLEANING`: rows 5 & 6 (`Gamma Co`) detected as an **exact** duplicate group → **report only**, keep/remove offered, never auto-deleted.
- `CANONICAL` (near-dup): rows 1 & 2 (`Acme Inc` ~ `"Acme, Inc."`) and rows 3 & 4 (`Beta LLC` ~ `beta llc`) flagged as **likely duplicates** via fuzzy match — surfaced for keep/remove. Proves fuzzy catches what exact-match can't. Nothing auto-deleted.

---

## 10 — `10_multisheet_json/` (`workbook.xlsx`, `nested.json`)  · Tier: hard
Multi-sheet Excel → multiple tables; nested JSON → flattened.
`workbook.xlsx` — **sheet `products`:**
```
product_id | name   | category_id | price
1          | Widget | 10          | 9.99
2          | Gadget | 20          | 19.99
```
`workbook.xlsx` — **sheet `categories`:**
```
category_id | category_name
10          | Tools
20          | Electronics
```
`nested.json`:
```json
[
  {"id": 1, "name": "Alice", "address": {"city": "NYC", "zip": "10001"}},
  {"id": 2, "name": "Bob",   "address": {"city": "LA"}}
]
```
**Assertions**
- `INGEST`: xlsx → **2 tables** (`products`, `categories`), one per sheet. JSON array-of-objects → 1 table; nested `address` flattened to `address.city`, `address.zip`; **ragged keys** handled (Bob's missing `zip` → NULL).
- `JOINS`: `products.category_id → categories.category_id` proposed.

---

## 11 — `11_degenerate/` (`empty.csv`, `header_only.csv`, `single_col.csv`)  · Tier: hard (graceful-failure)
Must **never crash** — fail clearly or proceed sensibly.
```csv
# empty.csv  →  (0 bytes, truly empty)
```
```csv
# header_only.csv
col_a,col_b
```
```csv
# single_col.csv
value
10
20
30
```
**Assertions**
- `INGEST`: `empty.csv` → clear "file is empty" message, **no table, no exception**. `header_only.csv` → table with schema + **0 rows**, no crash; pipeline proceeds/ warns. `single_col.csv` → valid 1-column table, type inferred (int), **no join candidates** generated.
- No fixture in this group raises an unhandled exception.

---

## 12 — `12_garbage_encoding/` (`weird_encoding.csv`, `not_tabular.csv`, large via builder)  · Tier: hardest
Encoding oddities, non-tabular garbage, and the sampling path.
- `weird_encoding.csv`: UTF-8 **BOM** + a Latin-1-encoded accented value (e.g., `José`). Mixed.
- `not_tabular.csv`: random non-tabular text / a renamed binary (not real CSV).
- **Large file**: generated by a fixture builder (`make_large_csv(rows=100_000)`) at test time — **do not commit** a huge file.
```csv
# weird_encoding.csv (conceptual — generate with BOM + latin-1 byte for é)
id,name
1,José
2,Zoë
```
**Assertions**
- `INGEST`: BOM stripped; encoding detected; `José`/`Zoë` decoded correctly (not mojibake). `not_tabular.csv` → detected as non-tabular, **rejected with a clear error**, no crash. Large file → ingest + profiling complete under a time budget using **sampling** (profiler must not full-scan 100k rows); no memory blowup.

---

## Test harness conventions
- One helper, e.g. `load_fixture(name) -> Pipeline`, runs ingest → cleaning → (canonical) and exposes `.tables`, `.ledger`, `.flags`.
- Assert against the **ChangeLedger** (rule fired, cells affected, reversibility) and the **DuckDB tables** (row counts, dtypes, distinct values), not against print output.
- Mark `PROFILER`/`JOINS`-tagged assertions with `@pytest.mark.joins` / `@pytest.mark.profiler` so Phase 1 can run cleaning-only (`pytest -m "not joins and not profiler"`) and they switch on when those engines land.
- Every degenerate/garbage case asserts **"does not raise"** explicitly.

## Done = 
All `INGEST` + `CLEANING` + `CANONICAL` assertions green (fixtures 01–12), with the hard ones (06, 07, 08, 09, 11, 12) passing — that's when the cleaning claim is earned. Then move to PROFILER/JOINS.
