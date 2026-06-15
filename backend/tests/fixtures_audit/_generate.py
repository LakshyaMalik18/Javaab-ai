"""
Regenerates every fixture data file deterministically.
Run from this directory:  python _generate.py
Most files are written as exact bytes so the messiness is reproduced precisely.
The 100k-row 'large' file is NOT written here; it is built at test time by _make_large.py.
"""
from pathlib import Path
import pandas as pd

HERE = Path(__file__).parent


def w(path: str, text: str, *, encoding="utf-8", newline="\n"):
    p = HERE / path
    p.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\n", newline)
    p.write_bytes(data.encode(encoding))


def wb(path: str, raw: bytes):
    p = HERE / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)


# 01 — clean baseline
w("01_clean_basic/data.csv",
"""order_id,product,quantity,price
1,Widget,3,9.99
2,Gadget,1,19.99
3,Widget,5,9.99
""")

# 02 — clean joinable pair
w("02_join_pair/customers.csv",
"""id,name,segment
1,Alice,Enterprise
2,Bob,SMB
3,Carol,Enterprise
""")
w("02_join_pair/orders.csv",
"""order_id,customer_id,amount
100,1,250.00
101,1,75.00
102,2,500.00
103,3,125.00
""")

# 03 — currency / percent / thousands / parentheses-negative
w("03_money_formats/data.csv",
'''item,revenue,discount,units
A,"$1,234.50",10%,"1,200"
B,"$987.00",5%,"950"
C,"($45.00)",0%,"3,400"
''')

# 04 — ambiguous dates (whole-column voting + a genuinely ambiguous column)
w("04_ambiguous_dates/data.csv",
"""event,date,date2
A,03/04/2026,01/02/2026
B,15/04/2026,03/02/2026
C,07/04/2026,05/02/2026
""")

# 05 — messy null tokens
w("05_messy_nulls/data.csv",
"""id,status,notes
1,active,ok
2,NA,
3,-,n/a
4,inactive,N/A
5,,none
""")

# 06 — coded/abbreviated headers (must join by VALUES not names)
w("06_coded_headers/cstm.csv",
"""cst_id,cst_nm,sgmt
1,Alice,ENT
2,Bob,SMB
3,Carol,ENT
""")
w("06_coded_headers/ordr.csv",
"""ord_id,cst_id,amt,ord_dt
100,1,250,03/04/2026
101,2,500,15/04/2026
102,1,125,07/04/2026
""")

# 07 — banner/preamble rows + 2-row header to flatten
w("07_preamble_header/data.csv",
"""ACME Corp - Sales Export
(do not distribute)

Region,2026,2026
Region,Revenue,Units
North,12000,300
South,9500,210
West,8000,150
""")

# 08 — canonicalization needed (USA / U.S.A. / America / United States)
w("08_canonicalize/data.csv",
"""customer,country,sales
A,USA,100
B,U.S.A.,200
C,America,150
D,United States,50
E,Canada,80
""")

# 09 — near-duplicate rows (fuzzy) + one exact dup
w("09_near_dupes/data.csv",
'''company,city,amount
Acme Inc,NYC,100
"Acme, Inc.",NYC,100
Beta LLC,LA,200
beta llc,LA,200
Gamma Co,SF,300
Gamma Co,SF,300
''')

# 10 — multi-sheet xlsx (2 tables) + nested json (flatten + ragged keys)
xlsx_path = HERE / "10_multisheet_json/workbook.xlsx"
xlsx_path.parent.mkdir(parents=True, exist_ok=True)
with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
    pd.DataFrame({
        "product_id": [1, 2],
        "name": ["Widget", "Gadget"],
        "category_id": [10, 20],
        "price": [9.99, 19.99],
    }).to_excel(xw, sheet_name="products", index=False)
    pd.DataFrame({
        "category_id": [10, 20],
        "category_name": ["Tools", "Electronics"],
    }).to_excel(xw, sheet_name="categories", index=False)
w("10_multisheet_json/nested.json",
"""[
  {"id": 1, "name": "Alice", "address": {"city": "NYC", "zip": "10001"}},
  {"id": 2, "name": "Bob", "address": {"city": "LA"}}
]
""")

# 11 — degenerate cases (must not crash)
wb("11_degenerate/empty.csv", b"")                       # truly empty
w("11_degenerate/header_only.csv", "col_a,col_b\n")      # header, no rows
w("11_degenerate/single_col.csv",
"""value
10
20
30
""")

# 12 — garbage / encoding torture
# weird_encoding.csv: UTF-8 BOM, then LATIN-1 body (José, Zoe-umlaut) -> mixed/weird on purpose
bom = b"\xef\xbb\xbf"
body = "id,name\n1,Jos\xe9\n2,Zo\xeb\n".encode("latin-1")  # \xe9 = é, \xeb = ë in latin-1
wb("12_garbage_encoding/weird_encoding.csv", bom + body)
# not_tabular.csv: clearly not a table (prose + stray bytes)
wb("12_garbage_encoding/not_tabular.csv",
   "This is not a spreadsheet. Just some notes.\nNo delimiters, no schema.\n\x00\x01random\x02bytes\n".encode("latin-1"))

print("All fixture data files generated.")
