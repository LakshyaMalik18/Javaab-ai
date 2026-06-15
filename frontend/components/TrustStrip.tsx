"use client";
// Live trust counters tick up as they enter view. Numbers come from mock metrics
// now; Phase 5B points this at GET /metrics (same shape).
import { useEffect, useRef, useState } from "react";
import { animate, useInView } from "framer-motion";
import { MOCK_METRICS } from "@/lib/mock";

function Counter({
  to,
  suffix = "",
  decimals = 0,
  start,
}: {
  to: number;
  suffix?: string;
  decimals?: number;
  start: boolean;
}) {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (!start) return;
    const c = animate(0, to, {
      duration: 1.6,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (x) => setV(x),
    });
    return () => c.stop();
  }, [start, to]);
  return (
    <span className="tnum">
      {v.toFixed(decimals)}
      {suffix}
    </span>
  );
}

export default function TrustStrip() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });
  const m = MOCK_METRICS;

  const items = [
    { label: "Queries answered", node: <Counter to={m.queries_answered} start={inView} /> },
    {
      label: "Destructive SQL blocked",
      node: <Counter to={m.destructive_blocked_pct} suffix="%" start={inView} />,
      accent: true,
    },
    {
      label: "Schema accuracy",
      node: <Counter to={m.schema_accuracy_pct} suffix="%" start={inView} />,
    },
    {
      label: "Bytes retained after session",
      node: <Counter to={m.bytes_retained} start={inView} />,
      accent: true,
    },
  ];

  return (
    <div
      ref={ref}
      className="grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-[var(--hairline)] bg-[var(--hairline)] md:grid-cols-4"
    >
      {items.map((it) => (
        <div key={it.label} className="bg-obsidian-800 px-6 py-8 text-center">
          <div
            className={`display text-[40px] sm:text-[48px] ${
              it.accent ? "accent" : "text-ink"
            }`}
          >
            {it.node}
          </div>
          <div className="mt-2 text-[12px] uppercase tracking-wider text-graphite">
            {it.label}
          </div>
        </div>
      ))}
    </div>
  );
}
