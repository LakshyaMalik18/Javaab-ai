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
from app.engines import nl2sql as nl2sql_mod
from app.engines import schema_contract as contract_mod
from app.llm import get_provider
from app.llm.base import LLMError, LLMProvider, RateLimitError
from app.models import AnswerResult, SchemaContract


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

        # 2. FAIL LOUD — unmapped question → refuse rather than invent SQL
        if not relevant.matched_anything:
            return AnswerResult(
                status="refused",
                question=question,
                clarifying_question=(
                    "I couldn't match your question to any column in the uploaded "
                    "data. Could you rephrase it using the available fields?"
                ),
            )

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

        # 4. generate SQL (lean prompt)
        try:
            nl = nl2sql_mod.generate_sql(question, contract, relevant, provider)
        except RateLimitError as e:
            return AnswerResult(status="error", question=question,
                                error=f"Rate limited by the model provider: {e}",
                                error_kind="rate_limit")
        except LLMError as e:
            return AnswerResult(status="error", question=question, error=str(e))

        # 5. FAIL LOUD — model asked for clarification or emitted no SQL
        if nl.needs_clarification or not nl.sql:
            return AnswerResult(
                status="clarify",
                question=question,
                assumptions=nl.assumptions,
                clarifying_question=(
                    nl.clarifying_question
                    or "I need more detail to answer that accurately. Can you clarify?"
                ),
            )

        # 6. GUARDRAIL — validate before any execution
        gr = guardrail_mod.validate_sql(
            nl.sql, contract.guardrail_schema(), max_rows=max_rows, metrics=metrics
        )
        if not gr.allowed:
            return AnswerResult(
                status="blocked",
                question=question,
                sql=nl.sql,
                assumptions=nl.assumptions,
                blocked_reason=gr.reason,
            )

        # 7. execute the validated SQL
        try:
            df = execute_mod.run_query(tables, gr.sql, con=con)
        except Exception as e:
            return AnswerResult(
                status="error", question=question, sql=gr.sql,
                error=f"Query execution failed: {e}",
            )

        # 8. merged insight + follow-ups
        ins = insight_mod.generate_insight(question, gr.sql, df, provider)

        return AnswerResult(
            status="answered",
            question=question,
            insight=ins.insight,
            sql=gr.sql,
            assumptions=nl.assumptions,
            followups=ins.followups,
            tables_used=gr.tables_used,
            columns=[str(c) for c in df.columns],
            rows=df.head(max_rows).to_dict(orient="records"),
        )
    except Exception as e:  # absolute backstop — never let it escape
        return AnswerResult(status="error", question=question, error=f"unexpected: {e}")


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
