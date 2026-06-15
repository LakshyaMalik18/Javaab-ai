"""Structure detection: encoding, delimiter, header discovery, multi-sheet Excel, JSON."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
from charset_normalizer import from_bytes


# ── Encoding detection ────────────────────────────────────────────────────────

def _decode_body(body: bytes) -> str:
    """Decode bytes, preferring UTF-8 but falling back to charset detection / latin-1.
    Handles the case where a BOM precedes a non-UTF-8 (e.g. latin-1) body."""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        best = from_bytes(body).best()
        return str(best) if best is not None else body.decode("latin-1")


def detect_and_decode(raw: bytes) -> str:
    # UTF-16 BOMs are unambiguous
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be")
    # UTF-8 BOM, but the body may actually be latin-1 (a real-world mess)
    if raw.startswith(b"\xef\xbb\xbf"):
        return _decode_body(raw[3:])
    return _decode_body(raw)


# ── Delimiter sniffing ────────────────────────────────────────────────────────

def sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        # fallback: count candidates on first non-empty line
        for line in text.splitlines():
            if line.strip():
                counts = {d: line.count(d) for d in (",", ";", "\t", "|")}
                return max(counts, key=counts.get)
        return ","


# ── Header discovery ──────────────────────────────────────────────────────────

_NULL_TOKENS = {"na", "n/a", "-", "--", "null", "none", "", "NULL", "n/a"}


def _is_header_like(row: list[str]) -> bool:
    """True if row looks like a header: mostly non-numeric strings."""
    if not row or all(v.strip() == "" for v in row):
        return False
    non_empty = [v.strip() for v in row if v.strip()]
    if not non_empty:
        return False
    numeric_count = sum(1 for v in non_empty if re.match(r"^-?\d+(\.\d+)?$", v))
    return numeric_count / len(non_empty) < 0.5


def _is_subheader_like(row: list[str]) -> bool:
    """Strict second-level header check: zero numeric cells (sub-field labels only)."""
    non_empty = [v.strip() for v in row if v.strip()]
    if not non_empty:
        return False
    return not any(re.match(r"^-?\d+(\.\d+)?$", v) for v in non_empty)


def discover_header(rows: list[list[str]]) -> tuple[int, int]:
    """Return (header_start_row, data_start_row).
    Skips preamble/banner rows using dominant column width.
    Detects multi-row (merged) headers only when the candidate row has empty cells.
    """
    from collections import Counter

    if not rows:
        return 0, 1

    width_counter = Counter(len(r) for r in rows if any(v.strip() for v in r))
    if not width_counter:
        return 0, 1
    dominant_width = width_counter.most_common(1)[0][0]

    # Single-column files: first row is always the header
    if dominant_width == 1:
        return 0, 1

    min_width = max(2, dominant_width // 2)

    for i in range(min(15, len(rows))):
        row = rows[i]
        if len(row) < min_width:
            continue  # too narrow — preamble/banner row
        if not _is_header_like(row):
            continue

        # Multi-row header: only trigger when this row has empty cells (merged groups)
        has_empty = any(v.strip() == "" for v in row)
        if has_empty and i + 1 < len(rows):
            next_row = rows[i + 1]
            if len(next_row) >= min_width and _is_subheader_like(next_row):
                return i, i + 2

        # Single-row header: require at least 2 unique non-empty cells to avoid data rows
        non_empty = [v.strip() for v in row if v.strip()]
        if len(set(non_empty)) >= min(len(non_empty), 2):
            return i, i + 1

    return 0, 1


def flatten_multirow_header(h1: list[str], h2: list[str]) -> list[str]:
    """Merge two header rows: use h1 group label to prefix h2 field where h1 non-empty."""
    result = []
    current_group = ""
    for a, b in zip(h1, h2):
        a, b = a.strip(), b.strip()
        if a:
            current_group = a
        label = f"{current_group}_{b}" if (current_group and b and current_group != b) else (b or current_group)
        result.append(label)
    return result


def _detect_footer_rows(df: pd.DataFrame) -> list[int]:
    """Return indices of likely footer rows (e.g., Total, Source, Note)."""
    junk_patterns = re.compile(r"^(total|grand total|subtotal|source|note|footnote|.*\*)", re.I)
    footer_rows = []
    for i in range(len(df) - 1, max(len(df) - 6, -1), -1):
        row = df.iloc[i]
        first_val = str(row.iloc[0]).strip() if len(row) > 0 else ""
        if junk_patterns.match(first_val):
            footer_rows.append(i)
        else:
            break
    return footer_rows


# ── Column name normalisation ─────────────────────────────────────────────────

def normalize_col_names(cols: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for i, c in enumerate(cols):
        name = re.sub(r"\s+", "_", c.strip().lower())
        name = re.sub(r"[^\w]", "_", name).strip("_") or f"col_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        result.append(name)
    return result


# ── CSV ingestion ─────────────────────────────────────────────────────────────

class IngestError(ValueError):
    pass


def ingest_csv(raw: bytes, table_name: str = "data") -> dict[str, Any]:
    """Return dict with keys: df, raw_headers, ambiguities, notes.

    A header-only file yields a valid 0-row table (not an error).
    Truly empty or non-tabular/binary input raises IngestError.
    """
    if not raw or not raw.strip():
        raise IngestError("File is empty.")
    # NUL/control bytes => binary or non-tabular content, not a CSV
    if b"\x00" in raw:
        raise IngestError("File does not appear to be tabular (binary/non-CSV content).")

    text = detect_and_decode(raw)
    if not text.strip():
        raise IngestError("File is empty.")

    delim = sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader]
    non_empty_rows = [r for r in rows if any(v.strip() for v in r)]

    if not non_empty_rows:
        raise IngestError("File contains no data rows.")

    header_row, data_start = discover_header(non_empty_rows)

    # Multi-row header detection
    if data_start - header_row == 2:
        raw_headers = flatten_multirow_header(
            non_empty_rows[header_row], non_empty_rows[header_row + 1]
        )
    else:
        raw_headers = [v.strip() for v in non_empty_rows[header_row]]

    norm_headers = normalize_col_names(raw_headers)
    ncols = len(raw_headers)

    data_rows = non_empty_rows[data_start:]
    if not data_rows:
        # Header-only file: valid schema, zero rows. Proceed gracefully.
        df = pd.DataFrame({c: pd.Series(dtype="object") for c in norm_headers})
        return {"df": df, "raw_headers": raw_headers, "ambiguities": [], "notes": ["No data rows."]}

    # Align row lengths
    padded = [r + [""] * max(0, ncols - len(r)) for r in data_rows]
    padded = [r[:ncols] for r in padded]

    df = pd.DataFrame(padded, columns=norm_headers)

    # Drop fully-empty columns (only meaningful when rows exist)
    df = df.replace("", pd.NA).dropna(axis=1, how="all").replace(pd.NA, "")

    # Detect and drop footer rows
    footer_idxs = _detect_footer_rows(df)
    if footer_idxs:
        df = df.drop(index=footer_idxs).reset_index(drop=True)

    return {"df": df, "raw_headers": raw_headers, "ambiguities": [], "notes": []}


# ── Excel ingestion ───────────────────────────────────────────────────────────

def ingest_excel(raw: bytes) -> dict[str, dict[str, Any]]:
    """Return {sheet_name: {df, raw_headers, ambiguities, notes}} for each sheet."""
    xf = pd.ExcelFile(io.BytesIO(raw), engine="openpyxl")
    results = {}
    for sheet in xf.sheet_names:
        df_raw = xf.parse(sheet, header=None, dtype=str).fillna("")
        rows = df_raw.values.tolist()
        rows = [[str(v) for v in r] for r in rows]
        non_empty = [r for r in rows if any(v.strip() for v in r)]
        if not non_empty:
            continue
        header_row, data_start = discover_header(non_empty)
        if data_start - header_row == 2:
            raw_headers = flatten_multirow_header(non_empty[header_row], non_empty[header_row + 1])
        else:
            raw_headers = [v.strip() for v in non_empty[header_row]]
        data_rows = non_empty[data_start:]
        if not data_rows:
            continue
        ncols = len(raw_headers)
        padded = [r + [""] * max(0, ncols - len(r)) for r in data_rows]
        padded = [r[:ncols] for r in padded]
        norm_headers = normalize_col_names(raw_headers)
        df = pd.DataFrame(padded, columns=norm_headers)
        df = df.replace("", pd.NA).dropna(axis=1, how="all").replace(pd.NA, "")
        footer_idxs = _detect_footer_rows(df)
        if footer_idxs:
            df = df.drop(index=footer_idxs).reset_index(drop=True)
        results[sheet] = {"df": df, "raw_headers": raw_headers, "ambiguities": [], "notes": []}
    return results


# ── JSON ingestion ────────────────────────────────────────────────────────────

def _flatten_obj(obj: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_obj(v, key))
        else:
            out[key] = v
    return out


def ingest_json(raw: bytes, table_name: str = "data") -> dict[str, Any]:
    text = detect_and_decode(raw)
    data = json.loads(text)
    if isinstance(data, list):
        records = [_flatten_obj(r) if isinstance(r, dict) else {"value": r} for r in data]
    elif isinstance(data, dict):
        records = [_flatten_obj(data)]
    else:
        raise IngestError("JSON root must be an array or object.")

    # Reconcile ragged keys — missing keys become None
    all_keys: list[str] = list(dict.fromkeys(k for r in records for k in r))
    aligned = [{k: r.get(k) for k in all_keys} for r in records]

    df = pd.DataFrame(aligned, columns=all_keys)
    norm_headers = normalize_col_names(all_keys)
    df.columns = norm_headers

    return {"df": df, "raw_headers": all_keys, "ambiguities": [], "notes": []}
