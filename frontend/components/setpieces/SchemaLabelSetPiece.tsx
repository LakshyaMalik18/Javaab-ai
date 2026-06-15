"use client";
// SET-PIECE 2 — "The Understanding". Coded headers (cst_id, ord_dt, amt) get
// plain-English meanings TYPING IN beside them; confidence meters fill.
// Reusable: feed it any TableContract.columns.
import { useRef } from "react";
import { motion, useInView } from "framer-motion";
import { MOCK_SCHEMA } from "@/lib/mock";
import type { ColumnContract } from "@/lib/types";
import { useTypewriter } from "@/lib/useTypewriter";

export default function SchemaLabelSetPiece({
  columns = MOCK_SCHEMA.tables[1].columns,
  tableName = "orders",
  auto = true,
}: {
  columns?: ColumnContract[];
  tableName?: string;
  auto?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });
  const start = auto && inView;

  return (
    <div ref={ref} className="glass-strong rounded-2xl p-5 shadow-glass sm:p-6">
      <div className="mb-4 flex items-center gap-2 text-[13px] text-graphite">
        <span className="h-1.5 w-1.5 rounded-full bg-indigo-glow shadow-glow" />
        Reading <span className="text-ink">{tableName}</span> — labelling{" "}
        {columns.length} columns
      </div>
      <div className="space-y-2.5">
        {columns.map((c, i) => (
          <Row key={c.name} col={c} start={start} delay={i * 0.45} />
        ))}
      </div>
    </div>
  );
}

function Row({
  col,
  start,
  delay,
}: {
  col: ColumnContract;
  start: boolean;
  delay: number;
}) {
  // Stagger each row's typing start via delay through a gated start flag.
  const gated = useDelayedStart(start, delay);
  const { out, done } = useTypewriter(col.meaning, gated, 55);
  const pct = Math.round(col.confidence * 100);
  const tone = col.confidence >= 0.7 ? "#E8B339" : col.confidence >= 0.5 ? "#F0C04A" : "#FF6B6B";

  return (
    <div className="grid grid-cols-[120px_1fr] items-center gap-4 rounded-lg border border-[var(--hairline)] bg-white/[0.02] px-3 py-2.5 sm:grid-cols-[140px_1fr_auto]">
      <code className="text-[13px] text-indigo-soft">{col.raw_name}</code>
      <div className="min-h-[18px] text-[13px] text-ink">
        {out}
        {gated && !done && <span className="caret" />}
        {col.provisional && done && (
          <span className="ml-2 rounded border border-amber-warm/30 bg-amber-warm/10 px-1.5 py-0.5 text-[10px] text-amber-warm">
            provisional
          </span>
        )}
      </div>
      <div className="col-span-2 flex items-center gap-2 sm:col-span-1">
        <div className="h-1.5 w-20 overflow-hidden rounded-full bg-white/10">
          <motion.div
            className="h-full rounded-full"
            style={{ background: tone, boxShadow: `0 0 12px ${tone}80` }}
            initial={{ width: 0 }}
            animate={{ width: start ? `${pct}%` : 0 }}
            transition={{ duration: 0.9, delay: delay + 0.2, ease: [0.16, 1, 0.3, 1] }}
          />
        </div>
        <span className="tnum text-[11px] text-graphite">{pct}%</span>
      </div>
    </div>
  );
}

// small helper: flips true `delay` seconds after `start`
import { useEffect, useState } from "react";
function useDelayedStart(start: boolean, delay: number) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!start) {
      setOn(false);
      return;
    }
    const t = setTimeout(() => setOn(true), delay * 1000);
    return () => clearTimeout(t);
  }, [start, delay]);
  return on;
}
