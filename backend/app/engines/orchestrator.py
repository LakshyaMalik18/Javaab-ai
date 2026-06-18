"""
orchestrator.py — Phase 3 query brain, end-to-end.

answer(question) runs: contract → relevance → (fail-loud check) → nl2sql →
guardrail → execute → insight, short-circuiting to a clarifying question or a
refusal whenever confidence is too low to answer honestly.

The FAIL-LOUD property is a first-class, deterministic gate here — it does NOT
rely on the LLM choosing to behave:
  - the question maps to nothing in the schema           → refuse
  - a column the question depends on is provisional      → ask its question
  - nl2sql itself asks for clarification                 → pass it through
  - the generated SQL fails the guardrail                → blocked, never run

A SessionBrain caches the (expensive) schema-labelling contract once per session
so it is not re-sent per question.
"""
from __future__ import annotations

import pandas as pd

from app.engines import execute as execute_mod
from app.engines import guardrail as guardrail_mod
from app.engines import insight as insight_mod
from app.engines import interpret as interpret_mod
from app.engines import nl2sql as nl2sql_mod
from app.engines import schema_contract as contract_mod
from app.llm import get_provider
from app.llm.base import LLMError, LLMProvider, RateLimitError
from app.models import AnswerResult, SchemaContract

# DuckDB raises these when generated SQL references a table/column that isn't in
# the data — i.e. hallucinated SQL that slipped past the static guardrail (e.g. an
# unqualified column the guardrail couldn't attribute). These must FAIL LOUD as a
# "couldn't map to your data" refusal — never surface as a destructive block, and
# never return fabricated rows.
_SCHEMA_MISMATCH_MARKERS = (
    "does not exist",
    "not found in from clause",
    "referenced column",
    "binder error",
    "catalog error",
)


def _looks_like_schema_mismatch(err: str) -> bool:
    e = err.lower()
    return any(m in e for m in _SCHEMA_MISMATCH_MARKERS)


def _couldnt_map(
    question: str,
    contract: SchemaContract,
    *,
    sql: str | None = None,
    detail: str | None = None,
) -> AnswerResult:
    """The fail-loud refusal for a question that can't be mapped to the schema.
    Helpful, not permissive: nothing executed, no rows returned — but we hand back
    real-schema example questions so the user has something to try."""
    msg = "I couldn't map your question to the uploaded data"
    if detail:
        msg += f" ({detail})"
    msg += ". Here are some questions I can answer from your data:"
    return AnswerResult(
        status="refused",
        question=question,
        sql=sql,
        clarifying_question=msg,
        suggestions=nl2sql_mod.suggest_questions(contract),
    )


def answer(
    question: str,
    tables: dict[str, pd.DataFrame],
    *,
    contract: SchemaContract | None = None,
    provider: LLMProvider | None = None,
    privacy_mode: bool = False,
    user_key: str | None = None,
    flags: list[dict] | None = None,
    metrics: guardrail_mod.GuardrailMetrics | None = None,
    max_rows: int = 500,
    con=None,
) -> AnswerResult:
    """Answer one question end-to-end. Never raises — every failure mode maps to
    a structured AnswerResult the UI can render."""
    provider = provider or get_provider(privacy_mode=privacy_mode, user_key=user_key)

    try:
        # 0. FAIL LOUD — destructive intent in the NL question. The model is
        # SELECT-constrained, so it never emits a DELETE for the SQL guardrail to
        # catch; we detect the intent here and block before any SQL is generated.
        block_reason = guardrail_mod.destructive_intent(question)
        if block_reason is not None:
            if metrics is not None:
                metrics.record({
                    "allowed": False,
                    "reason": "destructive intent in natural-language question",
                    "tables_used": [],
                })
            return AnswerResult(
                status="blocked",
                question=question,
                blocked_reason=block_reason,
            )

        if contract is None:
            contract = contract_mod.build_contract(tables, provider, flags=flags)

        # 1. what is this question about?
        relevant = nl2sql_mod.select_relevant(question, contract)

        # 2a. FAIL LOUD — a business synonym maps to two+ columns → ask which, never
        # guess. (Deterministic: decided before the model is ever called.)
        if relevant.clarify_question:
            return AnswerResult(
                status="clarify",
                question=question,
                clarifying_question=relevant.clarify_question,
            )

        # 2b. TIER 1 MISS → TIER 2 AI interpretation. Literal token-matching found
        # nothing, but a real user may be phrasing vaguely ("how many bond1 sold").
        # Instead of refusing, ask the LLM for a STRUCTURED MAPPING, re-validate it
        # against the real schema + data, and only then run it via the existing
        # generation/guardrail/execution path. Tier 1's behaviour on a hit is
        # completely unchanged.
        if not relevant.matched_anything:
            return _tier2_interpret(
                question, tables, contract, provider,
                metrics=metrics, max_rows=max_rows, con=con,
            )

        # ── TIER 1 HIT (deterministic mapping — unchanged) ───────────────────────
        # 3. FAIL LOUD — a column the question depends on is provisional/low-confidence
        prov = relevant.provisional_hits(contract)
        if prov:
            t, c = prov[0]
            cc = contract.table(t).column(c)
            return AnswerResult(
                status="clarify",
                question=question,
                clarifying_question=(
                    cc.clarifying_question
                    or f"I'm not fully sure what '{c}' means — can you confirm before I answer?"
                ),
            )

        # 4-8. generate → guardrail → execute → insight. On a no-SQL/clarify from the
        # model, Tier 1 asks for more detail (it DID map something deterministically).
        return _generate_and_run(
            question, tables, contract, relevant, provider,
            metrics=metrics, max_rows=max_rows, con=con,
            # On a model decline, forward any concrete runnable alternative it offered
            # (e.g. "drop buy records" → "show the buy records") as proposed_action so
            # the UI's "Yes — run it" chip re-submits it. None when the model offered
            # no actionable alternative (e.g. "which column?") → no chip renders.
            on_no_sql=lambda nl: AnswerResult(
                status="clarify",
                question=question,
                assumptions=nl.assumptions,
                clarifying_question=(
                    nl.clarifying_question
                    or "I need more detail to answer that accurately. Can you clarify?"
                ),
                proposed_action=nl.proposed_action,
            ),
        )
    except Exception as e:  # absolute backstop — never let it escape
        return AnswerResult(status="error", question=question, error=f"unexpected: {e}")


def _tier2_interpret(
    question: str,
    tables: dict[str, pd.DataFrame],
    contract: SchemaContract,
    provider: LLMProvider,
    *,
    metrics: guardrail_mod.GuardrailMetrics | None,
    max_rows: int,
    con,
) -> AnswerResult:
    """Tier-2 AI interpretation: the LLM proposes a structured mapping; the engine
    re-validates it and owns the answer. Three outcomes — answer (confident+valid),
    clarify (low-confidence/ambiguous), or fail loud (unmappable/hallucinated)."""
    try:
        proposal = interpret_mod.propose_mapping(question, contract, provider)
    except RateLimitError as e:
        return AnswerResult(status="error", question=question,
                            error=f"Rate limited by the model provider: {e}",
                            error_kind="rate_limit")
    except LLMError as e:
        return AnswerResult(status="error", question=question, error=str(e))

    # OUTCOME 3a — the AI says it can't map this to anything real (e.g. profit with
    # no cost column). Fail loud with suggestions; nothing runs.
    if proposal.unmappable or not proposal.tables:
        return _couldnt_map(question, contract, detail=proposal.reason)

    # SAFETY GATE — re-validate the proposal against the real schema + data BEFORE
    # any SQL is generated or run. A hallucinated table/column/value is rejected
    # here and never reaches execution.
    val = interpret_mod.validate_mapping(proposal, contract, tables)
    if not val.ok:
        return _couldnt_map(
            question, contract,
            detail=f"it referenced {val.missing}, which isn't in your data",
        )

    # OUTCOME 2 — low confidence or two+ plausible readings → ask, never silently
    # pick. No SQL runs until the user resolves it. We attach the AI's best-guess
    # restatement as `proposed_action` so the UI can offer a "Yes — run it" chip;
    # affirming just re-asks that concrete question on a fresh, stateless /ask.
    if interpret_mod.is_ambiguous(proposal):
        return AnswerResult(
            status="clarify",
            question=question,
            clarifying_question=interpret_mod.clarify_text(proposal),
            proposed_action=proposal.proposed_question,
        )

    # OUTCOME 1 — confident + valid → reuse the existing generation/guardrail/
    # execution path, anchored to the vetted mapping. The interpretation is shown in
    # `assumptions` (transparency) so a wrong guess is visible and correctable.
    return _generate_and_run(
        question, tables, contract, nl2sql_mod.Relevant.full(contract), provider,
        metrics=metrics, max_rows=max_rows, con=con,
        mapping_hint=val.note or None,
        extra_assumptions=[val.note] if val.note else None,
        # if the anchored generation still can't produce SQL, this was genuinely
        # unanswerable — fail loud, don't drop into a generic clarify.
        on_no_sql=lambda nl: _couldnt_map(question, contract, detail=nl.clarifying_question),
    )


def _generate_and_run(
    question: str,
    tables: dict[str, pd.DataFrame],
    contract: SchemaContract,
    relevant: "nl2sql_mod.Relevant",
    provider: LLMProvider,
    *,
    metrics: guardrail_mod.GuardrailMetrics | None,
    max_rows: int,
    con,
    on_no_sql,
    mapping_hint: str | None = None,
    extra_assumptions: list[str] | None = None,
) -> AnswerResult:
    """Shared tail for both tiers: generate SQL → guardrail → execute → insight.
    Every fail-loud branch (model declines, hallucinated schema at validate-time or
    execute-time) is preserved; only the no-SQL handling differs per tier."""
    try:
        nl = nl2sql_mod.generate_sql(
            question, contract, relevant, provider, mapping_hint=mapping_hint
        )
    except RateLimitError as e:
        return AnswerResult(status="error", question=question,
                            error=f"Rate limited by the model provider: {e}",
                            error_kind="rate_limit")
    except LLMError as e:
        return AnswerResult(status="error", question=question, error=str(e))

    if nl.needs_clarification or not nl.sql:
        return on_no_sql(nl)

    # GUARDRAIL — validate before any execution. Only the ACTIVE link per table-pair
    # is passed, so a join on an inactive (alternative) key is rejected as an invalid
    # join key — making the user's per-pair selection load-bearing at query time.
    gr = guardrail_mod.validate_sql(
        nl.sql, contract.guardrail_schema(), max_rows=max_rows, metrics=metrics,
        relationships=contract.active_relationships(),
    )
    if not gr.allowed:
        # hallucinated table/column, a cartesian/cross join, or an invalid join key →
        # fail loud as "couldn't map" (still never runs); a real non-SELECT stays a
        # destructive "read-only by design" block.
        if gr.kind in ("schema_mismatch", "cross_join", "invalid_join_key"):
            return _couldnt_map(question, contract, sql=nl.sql, detail=gr.reason)
        return AnswerResult(
            status="blocked", question=question, sql=nl.sql,
            assumptions=nl.assumptions, blocked_reason=gr.reason,
        )

    # COMPLETENESS GUARD — the SQL must join every table on the resolved join-path.
    # A query that omits a required bridge/endpoint is answering from a PARTIAL join
    # (e.g. dropping the customers table silently switches "enterprise revenue" to
    # "all revenue"). Refuse rather than return a confident wrong number. Only the
    # deterministic Tier-1 path carries a required set; Tier-2 (`.full()`) owns its
    # own validation and leaves it empty.
    required = {t.lower() for t in getattr(relevant, "required_tables", set())}
    if required:
        used = {t.lower() for t in gr.tables_used}
        missing = required - used
        if missing:
            return _couldnt_map(
                question, contract, sql=gr.sql,
                detail=(
                    "the generated query left out "
                    + ", ".join(sorted(missing))
                    + ", so it would answer from a partial join"
                ),
            )

    try:
        df = execute_mod.run_query(tables, gr.sql, con=con)
    except Exception as e:
        if _looks_like_schema_mismatch(str(e)):
            return _couldnt_map(question, contract, sql=gr.sql,
                                detail="referenced a field that isn't in your data")
        return AnswerResult(status="error", question=question, sql=gr.sql,
                            error=f"Query execution failed: {e}")

    ins = insight_mod.generate_insight(question, gr.sql, df, provider)
    assumptions = list(extra_assumptions or []) + list(nl.assumptions)
    return AnswerResult(
        status="answered",
        question=question,
        insight=ins.insight,
        sql=gr.sql,
        assumptions=assumptions,
        followups=ins.followups,
        tables_used=gr.tables_used,
        columns=[str(c) for c in df.columns],
        rows=df.head(max_rows).to_dict(orient="records"),
    )


class SessionBrain:
    """Holds the cached contract for a session so schema labelling is done once.
    (A thin precursor to the full Phase-4 session lifecycle.)"""

    def __init__(
        self,
        tables: dict[str, pd.DataFrame],
        *,
        provider: LLMProvider | None = None,
        privacy_mode: bool = False,
        user_key: str | None = None,
        flags: list[dict] | None = None,
    ) -> None:
        self.tables = tables
        self.privacy_mode = privacy_mode
        self.provider = provider or get_provider(privacy_mode=privacy_mode, user_key=user_key)
        self.metrics = guardrail_mod.GuardrailMetrics()
        # build (and cache) the contract ONCE
        self.contract = contract_mod.build_contract(tables, self.provider, flags=flags)

    def ask(self, question: str, **kwargs) -> AnswerResult:
        return answer(
            question,
            self.tables,
            contract=self.contract,
            provider=self.provider,
            metrics=self.metrics,
            **kwargs,
        )
