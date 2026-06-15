"use client";
// SET-PIECE 4 — "Question → Answer". Question types in, SQL types out, then the
// EXECUTIVE ORDER (§6): insight (amber) FIRST → chart builds → table settles.
// Reusable: feed it any AnswerResult.
import { useEffect, useRef, useState } from "react";
import { motion, useInView } from "framer-motion";
import { MOCK_ANSWER } from "@/lib/mock";
import type { AnswerResult } from "@/lib/types";
import { useTypewriter } from "@/lib/useTypewriter";
import ResultChart from "@/components/ResultChart";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";

export default function QuestionAnswerSetPiece({
  answer = MOCK_ANSWER,
  auto = true,
  compact = false,
}: {
  answer?: AnswerResult;
  auto?: boolean;
  compact?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });
  const reduced = usePrefersReducedMotion();
  const start = auto && inView;

  // staged reveal
  const [stage, setStage] = useState(0); // 0 q, 1 sql, 2 insight, 3 chart, 4 table
  const { out: q, done: qDone } = useTypewriter(answer.question, start, 38);
  const sqlStart = qDone && stage >= 1;
  const { out: sql } = useTypewriter(answer.sql ?? "", sqlStart, 120);

  useEffect(() => {
    if (qDone) setStage((s) => Math.max(s, 1));
  }, [qDone]);

  useEffect(() => {
    if (!sqlStart) return;
    const d = reduced ? 0 : 1;
    const t2 = setTimeout(() => setStage((s) => Math.max(s, 2)), d * 900);
    const t3 = setTimeout(() => setStage((s) => Math.max(s, 3)), d * 1500);
    const t4 = setTimeout(() => setStage((s) => Math.max(s, 4)), d * 2200);
    return () => {
      clearTimeout(t2);
      clearTimeout(t3);
      clearTimeout(t4);
    };
  }, [sqlStart, reduced]);

  const xKey = answer.columns[0] ?? "x";
  const yKey = answer.columns[1] ?? "y";

  return (
    <div ref={ref} className="space-y-4">
      {/* question */}
      <div className="glass rounded-xl px-4 py-3 text-[15px]">
        <span className="text-graphite">Ask: </span>
        <span className="text-ink">{q}</span>
        {!qDone && start && <span className="caret" />}
      </div>

      {/* SQL types out */}
      <AnimatePresenceBlock show={stage >= 1}>
        <div className="overflow-hidden rounded-xl border border-[var(--hairline)] bg-[#0d0d10]">
          <div className="border-b border-[var(--hairline)] px-4 py-2 text-[11px] uppercase tracking-wider text-graphite">
            generated SQL
          </div>
          <pre className="overflow-x-auto px-4 py-3 text-[12.5px] leading-relaxed text-indigo-soft">
            <code>{sql}</code>
          </pre>
        </div>
      </AnimatePresenceBlock>

      {/* INSIGHT FIRST (amber) — the hero of the answer */}
      <AnimatePresenceBlock show={stage >= 2}>
        <div className="relative overflow-hidden rounded-2xl border border-amber-warm/20 bg-gradient-to-b from-amber-warm/[0.08] to-amber-warm/[0.02] px-6 py-6">
          <div className="eyebrow mb-3 text-amber-warm/80">The answer</div>
          <p className="answer display text-[clamp(1.2rem,2.2vw,1.7rem)] font-medium leading-[1.22] tracking-tight">
            {answer.insight}
          </p>
          {answer.assumptions.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {answer.assumptions.map((a) => (
                <span
                  key={a}
                  className="rounded-md border border-[var(--hairline)] bg-white/5 px-2 py-1 text-[11px] text-graphite"
                >
                  {a}
                </span>
              ))}
            </div>
          )}
        </div>
      </AnimatePresenceBlock>

      {/* chart builds */}
      <AnimatePresenceBlock show={stage >= 3}>
        <div className="glass-strong rounded-xl p-4">
          <ResultChart
            type={(answer.chart_hint as any) ?? "bar"}
            rows={answer.rows}
            xKey={xKey}
            yKey={yKey}
          />
        </div>
      </AnimatePresenceBlock>

      {/* table settles last */}
      {!compact && (
        <AnimatePresenceBlock show={stage >= 4}>
          <div className="overflow-x-auto rounded-xl border border-[var(--hairline)]">
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
        </AnimatePresenceBlock>
      )}
    </div>
  );
}

function AnimatePresenceBlock({
  show,
  children,
}: {
  show: boolean;
  children: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={show ? { opacity: 1, y: 0 } : { opacity: 0, y: 14 }}
      transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
      style={{ pointerEvents: show ? "auto" : "none" }}
    >
      {show && children}
    </motion.div>
  );
}

function fmt(v: string | number | null) {
  if (v === null) return "—";
  if (typeof v === "number") return v.toLocaleString();
  return v;
}
