"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import type { AnswerResult, ChartHint } from "@/lib/types";
import ResultChart, { ChartType } from "@/components/ResultChart";
import { Pill } from "@/components/ui";

const CHART_TYPES: ChartType[] = ["bar", "line", "pie", "scatter", "table"];

export default function AnswerCard({
  answer,
  onFollowup,
}: {
  answer: AnswerResult;
  onFollowup: (q: string) => void;
}) {
  // ── Guardrail blocked card ────────────────────────────────────────────────
  if (answer.status === "blocked") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-2xl border border-red-400/25 bg-red-400/[0.06] p-5"
      >
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-red-400/15 text-red-300">
            ⛌
          </span>
          <span className="text-[14px] font-medium text-red-200">
            Blocked — read-only by design
          </span>
        </div>
        <p className="mt-3 text-[14px] leading-relaxed text-graphite">
          {answer.blocked_reason}
        </p>
        {answer.sql && (
          <pre className="mt-3 overflow-x-auto rounded-lg border border-[var(--hairline)] bg-[#0d0d10] px-3 py-2 text-[12.5px] text-red-300/80">
            <code>{answer.sql}</code>
          </pre>
        )}
        {answer.followups.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {answer.followups.map((f) => (
              <FollowupChip key={f} text={f} onClick={() => onFollowup(f)} />
            ))}
          </div>
        )}
      </motion.div>
    );
  }

  // ── Clarify / refused — the model asked instead of guessing ────────────────
  if (answer.status === "clarify" || answer.status === "refused") {
    const isRefused = answer.status === "refused";
    return (
      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-2xl border border-amber-warm/25 bg-amber-warm/[0.06] p-5"
      >
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-amber-warm/15 text-amber-warm">
            ?
          </span>
          <span className="text-[14px] font-medium text-amber-warm">
            {isRefused ? "I couldn't map that to your data" : "I need one detail first"}
          </span>
        </div>
        <p className="mt-3 text-[14px] leading-relaxed text-ink">
          {answer.clarifying_question ||
            "Could you rephrase that using the fields in your data?"}
        </p>
        {/* Affirmative shortcut: the backend proposed a concrete reading on this
            clarify, so "Yes — run it" re-asks that exact question (stateless). */}
        {answer.proposed_action && (
          <div className="mt-4">
            <button
              onClick={() => onFollowup(answer.proposed_action as string)}
              className="rounded-full bg-amber-warm/20 px-4 py-1.5 text-[12.5px] font-medium text-amber-warm transition hover:bg-amber-warm/30"
            >
              Yes — run it ✓
            </button>
            <p className="mt-1.5 text-[11.5px] text-graphite">
              Runs: “{answer.proposed_action}”
            </p>
          </div>
        )}
        {answer.assumptions.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {answer.assumptions.map((a) => (
              <span
                key={a}
                className="rounded-md border border-[var(--hairline)] bg-white/5 px-2.5 py-1 text-[11.5px] text-graphite"
              >
                {a}
              </span>
            ))}
          </div>
        )}
        {/* Suggested questions (the "here are some questions I can answer"
            chips) live in `suggestions` on a refusal — followups stays empty
            here, so render both and let the user click one to re-ask. */}
        {((answer.suggestions?.length ?? 0) > 0 || answer.followups.length > 0) && (
          <div className="mt-4 flex flex-wrap gap-2">
            {(answer.suggestions ?? []).map((s) => (
              <FollowupChip key={`s-${s}`} text={s} onClick={() => onFollowup(s)} />
            ))}
            {answer.followups.map((f) => (
              <FollowupChip key={`f-${f}`} text={f} onClick={() => onFollowup(f)} />
            ))}
          </div>
        )}
      </motion.div>
    );
  }

  // ── Error — execution / provider / rate-limit failures ─────────────────────
  if (answer.status === "error") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-2xl border border-red-400/25 bg-red-400/[0.06] p-5"
      >
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-red-400/15 text-red-300">
            !
          </span>
          <span className="text-[14px] font-medium text-red-200">
            That query didn&apos;t go through
          </span>
        </div>
        <p className="mt-3 text-[14px] leading-relaxed text-graphite">
          {answer.error ||
            "Something went wrong answering that. Please try again in a moment."}
        </p>
        {answer.sql && (
          <pre className="mt-3 overflow-x-auto rounded-lg border border-[var(--hairline)] bg-[#0d0d10] px-3 py-2 text-[12.5px] text-red-300/80">
            <code>{answer.sql}</code>
          </pre>
        )}
      </motion.div>
    );
  }

  return <AnsweredCard answer={answer} onFollowup={onFollowup} />;
}

function AnsweredCard({
  answer,
  onFollowup,
}: {
  answer: AnswerResult;
  onFollowup: (q: string) => void;
}) {
  const [chart, setChart] = useState<ChartType>(
    (answer.chart_hint as ChartType) ?? "bar",
  );
  const [sqlOpen, setSqlOpen] = useState(false);
  // Pick the measure (a numeric column) as the value axis, the first dimension as
  // the x axis, and a SECOND dimension (if present) as the series. Without this a
  // result like [buysell, bondid, total] would plot the bondid *dimension* as the
  // value, which is the multi-dimension chart bug.
  const { xKey, yKey, seriesKey } = deriveChartKeys(answer.columns, answer.rows);

  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-4"
    >
      {/* 1 · insight (amber) FIRST — the hero of the answer */}
      <div className="relative overflow-hidden rounded-3xl border border-amber-warm/20 bg-gradient-to-b from-amber-warm/[0.08] to-amber-warm/[0.02] p-7 sm:p-9">
        <div
          className="pointer-events-none absolute -right-20 -top-20 h-56 w-56 rounded-full opacity-60 blur-3xl"
          style={{ background: "radial-gradient(circle, rgba(240,192,74,0.18), transparent 70%)" }}
        />
        <div className="eyebrow mb-4 text-amber-warm/80">The answer</div>
        <p className="answer display max-w-3xl text-[clamp(1.4rem,2.8vw,2.1rem)] font-medium leading-[1.18] tracking-tight">
          {answer.insight}
        </p>
        {answer.assumptions.length > 0 && (
          <div className="mt-6 flex flex-wrap gap-2">
            {answer.assumptions.map((a) => (
              <span
                key={a}
                className="rounded-md border border-[var(--hairline)] bg-white/5 px-2.5 py-1 text-[11.5px] text-graphite"
              >
                {a}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 2 · chart with type dropdown */}
      {chart !== "table" && (
        <div className="glass-strong rounded-2xl p-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[12px] text-graphite">Visualization</span>
            <select
              value={chart}
              onChange={(e) => setChart(e.target.value as ChartType)}
              className="rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-2 py-1 text-[12px] text-ink"
            >
              {CHART_TYPES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <ResultChart
            type={chart}
            rows={answer.rows}
            xKey={xKey}
            yKey={yKey}
            seriesKey={seriesKey}
          />
        </div>
      )}

      {/* 3 · table */}
      <div className="overflow-hidden rounded-2xl border border-[var(--hairline)]">
        <div className="flex items-center justify-between border-b border-[var(--hairline)] px-4 py-2">
          <span className="text-[12px] text-graphite">
            Results · {answer.rows.length} rows
          </span>
          {chart === "table" && (
            <select
              value={chart}
              onChange={(e) => setChart(e.target.value as ChartType)}
              className="rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-2 py-1 text-[12px] text-ink"
            >
              {CHART_TYPES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead className="text-graphite">
              <tr>
                {answer.columns.map((c) => (
                  <th key={c} className="px-4 py-2.5 font-medium">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {answer.rows.map((row, i) => (
                <tr key={i} className="border-t border-[var(--hairline)]">
                  {answer.columns.map((c) => (
                    <td key={c} className="tnum px-4 py-2.5 text-ink">
                      {fmt(row[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 4 · collapsible SQL */}
      <div className="overflow-hidden rounded-2xl border border-[var(--hairline)]">
        <button
          onClick={() => setSqlOpen((v) => !v)}
          className="flex w-full items-center justify-between px-4 py-2.5 text-[12px] text-graphite transition hover:text-ink"
        >
          <span className="flex items-center gap-2">
            <Pill tone="indigo">SELECT</Pill> generated SQL
          </span>
          <span>{sqlOpen ? "−" : "+"}</span>
        </button>
        {sqlOpen && (
          <pre className="overflow-x-auto border-t border-[var(--hairline)] bg-[#0d0d10] px-4 py-3 text-[12.5px] leading-relaxed text-indigo-soft">
            <code>{answer.sql}</code>
          </pre>
        )}
      </div>

      {/* 5 · follow-up chips */}
      {answer.followups.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {answer.followups.map((f) => (
            <FollowupChip key={f} text={f} onClick={() => onFollowup(f)} />
          ))}
        </div>
      )}
    </motion.div>
  );
}

function FollowupChip({ text, onClick }: { text: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="rounded-full border border-indigo-glow/30 bg-indigo-glow/10 px-3 py-1.5 text-[12.5px] text-indigo-soft transition hover:bg-indigo-glow/20"
    >
      {text} →
    </button>
  );
}

function fmt(v: string | number | null) {
  if (v === null) return "—";
  if (typeof v === "number") return v.toLocaleString();
  return v;
}

// A column is a measure (value axis) if every non-null cell is a number.
function isNumericColumn(
  rows: Record<string, string | number | null>[],
  col: string,
): boolean {
  let seen = false;
  for (const r of rows) {
    const v = r[col];
    if (v === null || v === undefined) continue;
    if (typeof v !== "number") return false;
    seen = true;
  }
  return seen;
}

// Derive { xKey (1st dimension), yKey (the measure), seriesKey (2nd dimension) }
// so charts handle single- AND multi-dimension results correctly.
function deriveChartKeys(
  columns: string[],
  rows: Record<string, string | number | null>[],
): { xKey: string; yKey: string; seriesKey?: string } {
  const numeric = columns.filter((c) => isNumericColumn(rows, c));
  const dims = columns.filter((c) => !numeric.includes(c));
  const xKey = dims[0] ?? columns[0] ?? "x";
  const yKey =
    numeric[numeric.length - 1] ?? columns[1] ?? columns[0] ?? "y";
  const seriesKey =
    rows.length > 0 && dims.length >= 2 ? dims[1] : undefined;
  return { xKey, yKey, seriesKey };
}
