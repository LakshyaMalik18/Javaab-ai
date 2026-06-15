"""Run once to generate fixture files in tests/fixtures/."""
import csv, json, os
from pathlib import Path

import openpyxl
import pandas as pd

F = Path(__file__).parent / "fixtures"
F.mkdir(exist_ok=True)

# ── Fixture 1: clean single CSV, obvious types ────────────────────────────────
with open(F / "01_clean.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "name", "age", "salary", "joined"])
    w.writerows([
        [1, "Alice",   30, 70000,  "2021-03-15"],
        [2, "Bob",     25, 55000,  "2022-07-01"],
        [3, "Carol",   40, 90000,  "2019-11-20"],
    ])

# ── Fixture 2: two-file joinable set ─────────────────────────────────────────
with open(F / "02a_customers.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["customer_id", "name", "city"])
    w.writerows([[101, "Alice", "NYC"], [102, "Bob", "LA"], [103, "Carol", "Chicago"]])

with open(F / "02b_orders.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "cust_id", "amount", "order_date"])
    w.writerows([
        [1001, 101, 250.0,  "2024-01-10"],
        [1002, 102, 89.99,  "2024-01-15"],
        [1003, 101, 430.50, "2024-02-03"],
    ])

# ── Fixture 3: currency / percent / thousands separators ─────────────────────
with open(F / "03_currency.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["product", "price", "discount", "units_sold", "revenue"])
    w.writerows([
        ["Widget A", "$1,299.99", "15%",  "1,500",   "$1,949,985.00"],
        ["Widget B", "€899",      "5%",   "3,200",   "€2,876,800"],
        ["Widget C", "₹45,000",   "20%",  "750",     "₹33,750,000"],
        ["Widget D", "(£200)",    "0%",   "100",     "(£20,000)"],   # parens = negative
        ["Widget E", "$2.5M",     "10%",  "50",      "$125k"],
    ])

# ── Fixture 4: mixed / ambiguous date formats ─────────────────────────────────
with open(F / "04_dates.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["event", "event_date", "reported_date"])
    w.writerows([
        ["Launch",   "15/01/2024",  "01/15/2024"],   # DD/MM  vs  MM/DD
        ["Review",   "23/03/2024",  "03/23/2024"],   # 23 > 12 → DD/MM unambiguous
        ["Close",    "07/06/2024",  "06/07/2024"],
        ["Audit",    "30/11/2024",  "11/30/2024"],
        ["Release",  "01-Jan-2025", "January 5 2025"],
    ])

# ── Fixture 5: messy nulls ────────────────────────────────────────────────────
with open(F / "05_nulls.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "score", "comment"])
    w.writerows([
        [1,  88,    "Great"],
        [2,  "NA",  "Good"],
        [3,  "-",   ""],
        [4,  "n/a", "N/A"],
        [5,  "NULL","null"],
        [6,  "",    "--"],
        [7,  "none","None"],
        [8,  92,    "Excellent"],
    ])

# ── Fixture 6: coded/abbreviated headers ─────────────────────────────────────
with open(F / "06_coded_headers.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["cst_id", "ord_dt", "amt", "rgn", "prd_cd"])
    w.writerows([
        [101, "2024-01-10", 250.00, "NE", "WGT-A"],
        [102, "2024-01-15",  89.99, "SW", "WGT-B"],
        [101, "2024-02-03", 430.50, "NE", "WGT-A"],
    ])

# ── Fixture 7: banner/preamble rows + multi-row header ───────────────────────
with open(F / "07_banner.csv", "w", newline="") as f:
    w = csv.writer(f)
    # 3 banner rows before the real header
    w.writerow(["Quarterly Sales Report — Confidential"])
    w.writerow(["Generated: 2024-06-01"])
    w.writerow([])
    # multi-row header (row 1 = group, row 2 = field)
    w.writerow(["Customer", "",        "Order",   "Order"])
    w.writerow(["ID",       "Name",    "Date",    "Amount"])
    w.writerows([
        [101, "Alice", "2024-01-10", 250.00],
        [102, "Bob",   "2024-01-15",  89.99],
    ])
    # footer junk
    w.writerow(["Total", "", "", 339.99])

# ── Fixture 8: canonicalization needed ───────────────────────────────────────
with open(F / "08_canonicalize.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "country", "status"])
    w.writerows([
        [1, "USA",       "active"],
        [2, "U.S.A.",    "Active"],
        [3, "America",   "ACTIVE"],
        [4, "US",        "inactive"],
        [5, "United States", "Inactive"],
        [6, "UK",        "active"],
        [7, "U.K.",      "active"],
        [8, "Britain",   "Active"],
    ])

# ── Fixture 9: near-duplicate rows ───────────────────────────────────────────
with open(F / "09_near_dupes.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["company", "revenue", "year"])
    w.writerows([
        ["Acme Inc",    1_000_000, 2023],
        ["Acme, Inc.",  1_000_000, 2023],   # near-dup of above
        ["Widget Corp", 500_000,   2023],
        ["Widget Corp.", 500_000,  2023],   # near-dup
        ["Globex",      750_000,   2023],
    ])

# ── Fixture 10: multi-sheet Excel + JSON nested ───────────────────────────────
wb = openpyxl.Workbook()
ws1 = wb.active
ws1.title = "Customers"
ws1.append(["id", "name", "city"])
ws1.append([1, "Alice", "NYC"])
ws1.append([2, "Bob",   "LA"])
ws2 = wb.create_sheet("Orders")
ws2.append(["order_id", "cust_id", "amount"])
ws2.append([1001, 1, 250.0])
ws2.append([1002, 2, 89.99])
wb.save(F / "10_multisheet.xlsx")

nested = [
    {"id": 1, "name": "Alice", "address": {"city": "NYC", "zip": "10001"}, "score": 95},
    {"id": 2, "name": "Bob",   "address": {"city": "LA"},                  "score": 88},  # missing zip
    {"id": 3, "name": "Carol", "address": {"city": "Chicago", "zip": "60601"}},            # missing score
]
(F / "10_nested.json").write_text(json.dumps(nested, indent=2))

# ── Fixture 11: edge cases — empty / header-only / single-column ──────────────
(F / "11_empty.csv").write_text("")
(F / "11_header_only.csv").write_text("id,name,age\n")
with open(F / "11_single_col.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["notes"])
    w.writerows([["hello"], ["world"], ["foo"]])

# ── Fixture 12: encoding oddities ─────────────────────────────────────────────
# UTF-16 with BOM
rows_utf16 = "id,name\n1,Ångström\n2,Müller\n3,Café\n"
(F / "12_utf16.csv").write_bytes(b"\xff\xfe" + rows_utf16.encode("utf-16-le"))

# Latin-1
(F / "12_latin1.csv").write_bytes("id,name\n1,Ren\xe9\n2,Fran\xe7ois\n".encode("latin-1"))

# BOM UTF-8
(F / "12_utf8bom.csv").write_bytes(b"\xef\xbb\xbf" + b"id,name\n1,Alice\n2,Bob\n")

print(f"Fixtures written to {F}")
