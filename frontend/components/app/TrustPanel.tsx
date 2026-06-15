"use client";
import { useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useAppData } from "./AppStore";

export default function TrustPanel({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { metrics: m, metricsLoading, loadMetrics } = useAppData();

  useEffect(() => {
    if (open) loadMetrics();
  }, [open, loadMetrics]);

  const items = m
    ? [
        {
          label: "Queries answered (session)",
          value: m.queries_answered.toLocaleString(),
        },
        {
          label: "Destructive SQL blocked",
          value: `${Math.round(m.destructive_blocked_pct)}%`,
          accent: true,
        },
        // schema accuracy is only shown when /metrics serves a real figure — no invented stats
        ...(m.schema_accuracy_pct > 0
          ? [{ label: "Schema accuracy (test set)", value: `${m.schema_accuracy_pct}%` }]
          : []),
        { label: "Bytes retained after session", value: m.bytes_retained, accent: true },
      ]
    : [];

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/50"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            className="fixed right-0 top-0 z-50 h-full w-full max-w-md overflow-y-auto border-l border-[var(--hairline)] bg-obsidian-800 p-6"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "tween", duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="flex items-center justify-between">
              <h2 className="display text-[22px] text-ink">Trust panel</h2>
              <button onClick={onClose} className="text-graphite hover:text-ink">
                ✕
              </button>
            </div>
            <p className="mt-2 text-[12px] text-graphite">
              Every number from a real log — no invented stats.
            </p>

            {!m && metricsLoading && (
              <p className="mt-6 text-[13px] text-graphite">Loading live metrics…</p>
            )}
            {!m && !metricsLoading && (
              <p className="mt-6 text-[13px] text-graphite">
                Metrics will appear once your session is active.
              </p>
            )}

            <div className="mt-6 space-y-3">
              {items.map((it) => (
                <div
                  key={it.label}
                  className="flex items-center justify-between rounded-xl border border-[var(--hairline)] bg-white/[0.02] px-4 py-4"
                >
                  <span className="text-[13px] text-graphite">{it.label}</span>
                  <span
                    className={`tnum display text-[26px] ${
                      it.accent ? "accent" : "text-ink"
                    }`}
                  >
                    {it.value}
                  </span>
                </div>
              ))}
            </div>

            <div className="mt-6 flex items-center gap-2 rounded-xl border border-indigo-glow/20 bg-indigo-glow/[0.06] px-4 py-3 text-[12.5px] text-indigo-soft">
              <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-glow shadow-glow" />
              Session live · DuckDB in memory · wiped on exit
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
