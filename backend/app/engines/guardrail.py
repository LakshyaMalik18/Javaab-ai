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
) -> GuardrailResult:
    """Validate one SQL string against the session schema. Never raises."""
    schema_norm = {_norm(t): {_norm(c) for c in cols} for t, cols in schema.items()}

    result = _validate(sql, schema_norm, max_rows)
    if metrics is not None:
        metrics.record(result.as_decision())
    return result


def _block(reason: str) -> GuardrailResult:
    return GuardrailResult(allowed=False, reason=reason)


def _validate(sql: str, schema: dict[str, set[str]], max_rows: int) -> GuardrailResult:
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
            return _block(f"unknown table: {t}")
        if t not in tables_used:
            tables_used.append(t)

    # validate columns that are qualified or unambiguous
    ok, bad_col = _validate_columns(stmt, schema, tables_used, cte_names)
    if not ok:
        return _block(f"unknown column: {bad_col}")

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
