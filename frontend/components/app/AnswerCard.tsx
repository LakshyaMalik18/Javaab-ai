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
  const xKey = answer.columns[0] ?? "x";
  const yKey = answer.columns[1] ?? "y";

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
          <ResultChart type={chart} rows={answer.rows} xKey={xKey} yKey={yKey} />
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
