"""
guardrail.py — §6.2 SQL guardrail. Runs BEFORE any execution.

Hard rules (any failure => blocked):
  - parses with sqlglot (duckdb dialect)
  - exactly ONE statement
  - that statement is read-only: a SELECT (or a WITH ... SELECT)
  - no destructive / side-effecting verb anywhere
    (DELETE/DROP/UPDATE/INSERT/ALTER/ATTACH/COPY/PRAGMA/CREATE/REPLACE/TRUNCATE/
     GRANT/MERGE/CALL/EXPORT/INSTALL/LOAD/SET/VACUUM/DETACH)
  - every referenced table is in the session whitelist
  - every referenced column exists in the session schema
  - LIMIT <= max_rows, injected if absent

Acceptance bar (§10): 100% of destructive queries blocked.

Every decision is logged to an in-memory metrics store — query metadata only,
never user data — so the live trust panel reads from real numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

_DEFAULT_MAX_ROWS = 500

# Natural-language destructive intent — caught BEFORE SQL generation. Because the
# model is SELECT-constrained it will never emit a DELETE for the SQL guardrail to
# catch; "delete all orders" would otherwise fall through to a generic clarify.
# So we detect the *intent* in the question and route it straight to the blocked
# card. Tuned to avoid analytical false positives ("where revenue dropped",
# "remove duplicates from the view") while catching clear write/destroy phrasing.
_DESTRUCTIVE_INTENT = re.compile(
    r"""
      \b delete \b
    | \b truncate \b
    | \b wipe \b
    | \b erase \b
    | \b purge \b
    | \b destroy \b
    | \b overwrite \b
    | \b drop \s+ (?: table | tables | column | columns | database | the | all | every ) \b
    | \b (?: remove | get \s+ rid \s+ of ) \s+ (?: all | every | everything | the \s+ \w+ ) \b
    | \b (?: update | insert \s+ into | alter ) \s+ \w+ \s+ (?: set | values | add | drop ) \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_READONLY_MESSAGE = (
    "Javaab is read-only by design — it only runs SELECT queries, so destructive "
    "operations like delete, drop, truncate or wipe aren't allowed. Your data is "
    "never modified. Try asking a question about the data instead."
)


def destructive_intent(question: str) -> str | None:
    """Return a block reason if the NL question expresses destructive intent,
    else None. Independent of (and prior to) SQL generation."""
    if question and _DESTRUCTIVE_INTENT.search(question):
        return _READONLY_MESSAGE
    return None

# statement node types that are read-only
_READONLY_STMT = (exp.Select, exp.Union, exp.Except, exp.Intersect)

# any of these expression types anywhere => destructive / side-effecting
_FORBIDDEN_NODES = (
    exp.Delete, exp.Drop, exp.Update, exp.Insert, exp.Alter,
    exp.Create, exp.Command, exp.Pragma, exp.Set, exp.Merge,
)
# DuckDB verbs sqlglot parses as Command/keyword-only — caught by raw scan too
_FORBIDDEN_KEYWORDS = (
    "delete", "drop", "update", "insert", "alter", "create", "replace",
    "truncate", "attach", "detach", "copy", "pragma", "grant", "revoke",
    "merge", "call", "export", "import", "install", "load", "vacuum",
    "set ", "reindex",
)


@dataclass
class GuardrailMetrics:
    """In-memory, session-scoped. Metadata only — no user data ever stored."""
    allowed: int = 0
    blocked: int = 0
    decisions: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.allowed + self.blocked

    @property
    def pct_blocked(self) -> float:
        return (self.blocked / self.total * 100.0) if self.total else 0.0

    def record(self, decision: dict) -> None:
        if decision["allowed"]:
            self.allowed += 1
        else:
            self.blocked += 1
        # store metadata only
        self.decisions.append({
            "allowed": decision["allowed"],
            "reason": decision["reason"],
            "tables_used": decision.get("tables_used", []),
        })


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str
    sql: str | None = None          # possibly LIMIT-injected, ready to run
    tables_used: list[str] = field(default_factory=list)
    #: why it was blocked, so callers can label it correctly.
    #:   "ok"             — allowed
    #:   "destructive"    — non-SELECT / forbidden verb / multi-statement → "read-only by design"
    #:   "schema_mismatch"— references a table/column not in the schema (hallucinated SQL)
    #:                      → must still fail loud, but is NOT a destructive block
    #:   "cross_join"     — a cartesian / cross join with no join condition → fail loud
    #:                      (a partial/nonsensical join, not a destructive block)
    #:   "invalid_join_key"— a JOIN welds tables on columns that are NOT a discovered
    #:                      FK→PK key → fail loud (wrong-key join, silent-0-row risk)
    kind: str = "ok"

    def as_decision(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "sql": self.sql,
            "tables_used": self.tables_used,
        }


def _norm(name: str) -> str:
    return str(name).strip().strip('"').lower()


def validate_sql(
    sql: str,
    schema: dict[str, set[str]] | dict[str, list[str]],
    max_rows: int = _DEFAULT_MAX_ROWS,
    metrics: GuardrailMetrics | None = None,
    relationships=None,
) -> GuardrailResult:
    """Validate one SQL string against the session schema. Never raises.

    `relationships` (optional) is the discovered FK→PK edge set — an iterable of
    RelationshipEdge-like objects or dicts with from_table/from_col/to_table/to_col.
    When provided, every inter-table JOIN condition must match a discovered key
    (see the INVALID-JOIN-KEY guard). Omitted (None) → that check is skipped, so
    existing callers/tests are unaffected."""
    schema_norm = {_norm(t): {_norm(c) for c in cols} for t, cols in schema.items()}
    edges = _normalize_edges(relationships) if relationships is not None else None

    result = _validate(sql, schema_norm, max_rows, edges)
    if metrics is not None:
        metrics.record(result.as_decision())
    return result


def _block(reason: str, kind: str = "destructive") -> GuardrailResult:
    return GuardrailResult(allowed=False, reason=reason, kind=kind)


def _validate(
    sql: str,
    schema: dict[str, set[str]],
    max_rows: int,
    edges: set[frozenset] | None = None,
) -> GuardrailResult:
    raw = (sql or "").strip()
    if not raw:
        return _block("empty query")

    # cheap raw-text guard for verbs sqlglot may normalize away
    lowered = raw.lower()
    for kw in _FORBIDDEN_KEYWORDS:
        token = kw.strip()
        if _has_keyword(lowered, token):
            return _block(f"contains forbidden keyword: {token.upper()}")

    # parse — multiple statements / syntax errors are blocked
    try:
        statements = sqlglot.parse(raw, read="duckdb")
    except Exception as e:
        return _block(f"unparseable SQL: {e}")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return _block("only a single statement is allowed")

    stmt = statements[0]

    # unwrap WITH (CTE) to find the underlying statement
    inner = stmt
    if isinstance(inner, exp.Select) and inner.args.get("with"):
        pass  # SELECT with CTEs is fine
    if not isinstance(inner, _READONLY_STMT):
        return _block("only read-only SELECT queries are allowed")

    # any forbidden node anywhere (e.g. subquery DML) => block
    for node in stmt.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, _FORBIDDEN_NODES):
            return _block(f"contains a non-SELECT operation: {type(node).__name__}")

    # collect referenced tables and validate against whitelist
    referenced = _table_names(stmt)
    cte_names = {_norm(c.alias_or_name) for c in stmt.find_all(exp.CTE)}
    tables_used: list[str] = []
    for t in referenced:
        if t in cte_names:
            continue
        if t not in schema:
            return _block(f"unknown table: {t}", kind="schema_mismatch")
        if t not in tables_used:
            tables_used.append(t)

    # validate columns that are qualified or unambiguous
    ok, bad_col = _validate_columns(stmt, schema, tables_used, cte_names)
    if not ok:
        return _block(f"unknown column: {bad_col}", kind="schema_mismatch")

    # CARTESIAN GUARD — a comma join (FROM a, b) or an explicit CROSS JOIN with no
    # ON/USING multiplies rows and is never what an analytics question means. Catch
    # it before execution so a partial/nonsensical join can't return a confident
    # wrong number. (Joins to a subquery/unnest aren't base-table cartesians.)
    if _has_cross_join(stmt):
        return _block(
            "joins two tables with no join condition (cartesian / cross join)",
            kind="cross_join",
        )

    # INVALID-JOIN-KEY GUARD — every inter-table JOIN condition must weld the tables
    # on a DISCOVERED relationship key (a known FK→PK edge). A join that uses the
    # right tables but the WRONG key columns (e.g. `district.k2 = region.f1` when the
    # real key is `district.f2 = region.k1`) parses fine, references real columns and
    # has an ON clause — so it slips past every other guard — yet matches zero rows
    # and returns a silent, empty/wrong answer. Catch it here. Skipped when no edge
    # set was supplied.
    if edges is not None:
        bad = _invalid_join_key(stmt, _alias_table_map(stmt, schema), edges)
        if bad:
            return _block(bad, kind="invalid_join_key")

    # defense-in-depth: make text-literal equality case-insensitive so a casing
    # mismatch (status = 'paid' vs a stray 'Paid') can never silently drop rows
    stmt = _case_insensitive_text_eq(stmt)

    # enforce / inject LIMIT
    final_sql = _enforce_limit(stmt, max_rows)

    return GuardrailResult(
        allowed=True, reason="ok", sql=final_sql, tables_used=tables_used
    )


def _has_keyword(lowered_sql: str, token: str) -> bool:
    import re
    return re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", lowered_sql) is not None


def _has_cross_join(stmt: exp.Expression) -> bool:
    """True if any JOIN against a base table lacks a join condition — a comma join
    (`FROM a, b`) or an explicit `CROSS JOIN`. Joins onto a subquery/unnest/values
    expression are not base-table cartesians and are left alone."""
    for j in stmt.find_all(exp.Join):
        if not isinstance(j.this, exp.Table):
            continue
        if j.args.get("on") or j.args.get("using"):
            continue
        return True
    return False


def _normalize_edges(relationships) -> set[frozenset]:
    """Discovered relationships → a set of undirected, normalised key pairs:
    {frozenset({(table, col), (table, col)}), ...}. Accepts edge objects (with
    from_table/from_col/to_table/to_col attributes) or dicts."""
    edges: set[frozenset] = set()
    for e in relationships or []:
        if isinstance(e, dict):
            ft, fc, tt, tc = e["from_table"], e["from_col"], e["to_table"], e["to_col"]
        else:
            ft, fc, tt, tc = e.from_table, e.from_col, e.to_table, e.to_col
        edges.add(frozenset({(_norm(ft), _norm(fc)), (_norm(tt), _norm(tc))}))
    return edges


def _alias_table_map(stmt: exp.Expression, schema: dict[str, set[str]]) -> dict[str, str]:
    """alias / table-name → real base-table name, for resolving qualified columns."""
    amap: dict[str, str] = {}
    for tbl in stmt.find_all(exp.Table):
        tname = _norm(tbl.name)
        if tname in schema:
            amap[_norm(tbl.alias_or_name)] = tname
            amap[tname] = tname
    return amap


def _col_ref(node: exp.Expression, amap: dict[str, str]) -> tuple[str, str] | None:
    """A qualified column → (base_table, column), else None (unqualified columns
    can't be attributed to a table reliably, so they aren't key-checked)."""
    if not isinstance(node, exp.Column) or not node.table:
        return None
    tgt = amap.get(_norm(node.table))
    return (tgt, _norm(node.name)) if tgt else None


def _invalid_join_key(
    stmt: exp.Expression, amap: dict[str, str], edges: set[frozenset]
) -> str | None:
    """Return a reason if any inter-table JOIN condition welds two tables on a pair
    of columns that is NOT a discovered relationship key; else None.

    A join must have at least ONE inter-table column=column equality that matches a
    known edge. Column=literal predicates and same-table comparisons are ignored
    (they aren't join keys); USING(col) must correspond to an edge on that column."""
    for j in stmt.find_all(exp.Join):
        if not isinstance(j.this, exp.Table):
            continue                       # join onto a subquery/unnest — not a base key
        rt = _norm(j.this.name)
        if rt not in amap.values():        # not a whitelisted base table
            continue

        using = j.args.get("using")
        if using:
            for u in using:
                cn = _norm(getattr(u, "name", "") or "")
                if cn and not any((rt, cn) in pair for pair in edges):
                    return f"join key {rt}.{cn} (USING) is not a discovered relationship"
            continue

        on = j.args.get("on")
        if on is None:
            continue                       # missing ON → handled by the cartesian guard

        inter_pairs: list[frozenset] = []
        for eq in on.find_all(exp.EQ):
            a = _col_ref(eq.this, amap)
            b = _col_ref(eq.expression, amap)
            if a and b and a[0] and b[0] and a[0] != b[0]:
                inter_pairs.append(frozenset({a, b}))

        if not inter_pairs:
            continue                       # no checkable inter-table key (e.g. col=literal)
        if not any(p in edges for p in inter_pairs):
            (t1, c1), (t2, c2) = tuple(next(iter(inter_pairs)))
            return (
                f"join {t1}.{c1} = {t2}.{c2} is not a discovered relationship key "
                f"(the tables aren't linked on those columns)"
            )
    return None


def _table_names(stmt: exp.Expression) -> list[str]:
    names = []
    for tbl in stmt.find_all(exp.Table):
        names.append(_norm(tbl.name))
    return names


def _validate_columns(
    stmt: exp.Expression,
    schema: dict[str, set[str]],
    tables_used: list[str],
    cte_names: set[str],
) -> tuple[bool, str | None]:
    # build alias -> table map
    alias_map: dict[str, str] = {}
    for tbl in stmt.find_all(exp.Table):
        tname = _norm(tbl.name)
        if tname in schema:
            alias_map[_norm(tbl.alias_or_name)] = tname
            alias_map[tname] = tname

    all_cols: set[str] = set()
    for t in tables_used:
        all_cols |= schema[t]

    for col in stmt.find_all(exp.Column):
        cname = _norm(col.name)
        if cname == "*" or col.is_star:
            continue
        tbl_ref = _norm(col.table) if col.table else None
        if tbl_ref:
            target = alias_map.get(tbl_ref)
            if target is None:
                if tbl_ref in cte_names:
                    continue  # column from a CTE projection — trust it
                return False, f"{tbl_ref}.{cname}"
            if cname not in schema[target]:
                return False, f"{tbl_ref}.{cname}"
        else:
            # unqualified: must exist in at least one referenced table
            if all_cols and cname not in all_cols:
                # could be an alias from the SELECT projection; allow if defined
                if cname in _projection_aliases(stmt):
                    continue
                return False, cname
    return True, None


def _projection_aliases(stmt: exp.Expression) -> set[str]:
    aliases = set()
    for a in stmt.find_all(exp.Alias):
        aliases.add(_norm(a.alias_or_name))
    return aliases


def _has_alpha(s: str) -> bool:
    return any(c.isalpha() for c in s)


def _fold_literal(col: exp.Expression, lit: exp.Expression) -> tuple[exp.Expression, exp.Expression] | None:
    """If `col` is a column and `lit` is an alphabetic string literal, return
    (LOWER(col), lowercased-literal). Returns None otherwise.

    Restricting to literals that contain a letter keeps this off numeric and
    date literals ('2024-01-01' has no alpha) and off columns already wrapped in
    a function — so dates/numbers and existing LOWER(...) calls are untouched."""
    if not isinstance(col, exp.Column):
        return None
    if not (isinstance(lit, exp.Literal) and lit.is_string and _has_alpha(lit.name)):
        return None
    return exp.Lower(this=col.copy()), exp.Literal.string(lit.name.lower())


def _case_insensitive_text_eq(stmt: exp.Expression) -> exp.Expression:
    """Rewrite `col = 'Txt'` / `col <> 'Txt'` / `col IN ('A','b')` to compare on
    LOWER(col) against lowercased string literals. Equality only — range
    operators (<, >, >=, <=) and non-string comparisons are left alone."""

    def _transform(node: exp.Expression) -> exp.Expression:
        if isinstance(node, (exp.EQ, exp.NEQ)):
            for left, right, swap in (
                (node.this, node.expression, False),
                (node.expression, node.this, True),
            ):
                folded = _fold_literal(left, right)
                if folded is not None:
                    low_col, low_lit = folded
                    this, other = (low_col, low_lit) if not swap else (low_lit, low_col)
                    return node.__class__(this=this, expression=other)
        elif isinstance(node, exp.In):
            col = node.this
            values = node.expressions
            if (
                isinstance(col, exp.Column)
                and values
                and all(isinstance(v, exp.Literal) and v.is_string for v in values)
                and any(_has_alpha(v.name) for v in values)
            ):
                new_vals = [exp.Literal.string(v.name.lower()) for v in values]
                return exp.In(this=exp.Lower(this=col.copy()), expressions=new_vals)
        return node

    return stmt.transform(_transform)


def _enforce_limit(stmt: exp.Expression, max_rows: int) -> str:
    limit = stmt.args.get("limit")
    if limit is None:
        stmt = stmt.limit(max_rows)
    else:
        try:
            current = int(limit.expression.name)
            if current > max_rows:
                stmt.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        except (AttributeError, ValueError, TypeError):
            stmt.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return stmt.sql(dialect="duckdb")
