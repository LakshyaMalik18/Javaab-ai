"use client";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import CleanSetPiece from "@/components/setpieces/CleanSetPiece";
import { Pill } from "@/components/ui";
import { useAppData } from "./AppStore";

export default function CleaningStep({ onNext }: { onNext: () => void }) {
  const { report } = useAppData();
  const [undone, setUndone] = useState<Set<number>>(new Set());
  const [addOpen, setAddOpen] = useState(false);
  const [dupChoice, setDupChoice] = useState<Record<number, "keep" | "remove">>({});
  const [showAllDupes, setShowAllDupes] = useState(false);

  const ledger = report?.ledger ?? [];
  const duplicates = report?.duplicates ?? [];
  const ambiguity = report?.ambiguity ?? [];

  // Near-dup lists can be long on transactional data; show a capped window with a
  // "view more" rather than dumping every pair into a minute-long scroll.
  const DUPE_CAP = 8;
  const visibleDuplicates = showAllDupes ? duplicates : duplicates.slice(0, DUPE_CAP);
  const hiddenDupeCount = duplicates.length - visibleDuplicates.length;

  const toggleUndo = (i: number) =>
    setUndone((s) => {
      const n = new Set(s);
      n.has(i) ? n.delete(i) : n.add(i);
      return n;
    });

  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-6">
        <h1 className="display text-[clamp(2.2rem,5vw,3.4rem)] text-ink">
          Here&apos;s what I cleaned.
        </h1>
        <p className="mt-2 text-[15px] text-graphite">
          Every change is reversible. Undo any rule, or add your own and re-run.
        </p>
      </header>

      {/* the live set-piece on (mock) real cells */}
      <CleanSetPiece />

      {/* ambiguity flags */}
      {ambiguity.length > 0 && (
        <div className="mt-6 space-y-2">
          {ambiguity.map((a) => (
            <div
              key={a.column}
              className="flex items-start gap-3 rounded-xl border border-amber-warm/20 bg-amber-warm/[0.05] px-4 py-3 text-[13px]"
            >
              <Pill tone="amber">resolved</Pill>
              <span className="text-graphite">
                <span className="text-ink">{a.column}</span> — {a.detail}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ledger grouped by rule */}
      <div className="mt-8 flex items-center justify-between">
        <h2 className="text-[13px] uppercase tracking-wider text-graphite">
          Change ledger
        </h2>
        <button
          onClick={() => setAddOpen((v) => !v)}
          className="rounded-full border border-[var(--hairline)] px-3 py-1.5 text-[12px] text-indigo-soft transition hover:bg-white/5"
        >
          + Add rule
        </button>
      </div>

      <AnimatePresence>
        {addOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-3 overflow-hidden"
          >
            <div className="grid grid-cols-1 gap-3 rounded-xl border border-[var(--hairline)] bg-white/[0.02] p-4 sm:grid-cols-4">
              <select className="rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-3 py-2 text-[13px] text-ink">
                <option>Add null token</option>
                <option>Force column type</option>
                <option>Set date format</option>
                <option>Find / replace</option>
                <option>Canonical mapping</option>
              </select>
              <input
                placeholder="column"
                className="rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-3 py-2 text-[13px] text-ink placeholder:text-graphite/60"
              />
              <input
                placeholder="value / pattern"
                className="rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-3 py-2 text-[13px] text-ink placeholder:text-graphite/60"
              />
              <button
                onClick={() => setAddOpen(false)}
                className="rounded-lg bg-indigo-glow px-3 py-2 text-[13px] font-medium text-white transition hover:brightness-110"
              >
                Re-run cleaning
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {ledger.length === 0 && (
        <p className="mt-3 rounded-xl border border-[var(--hairline)] bg-white/[0.02] px-4 py-3 text-[13px] text-graphite">
          Your data came in clean — no transformations were needed.
        </p>
      )}

      <div className="mt-3 space-y-2">
        {ledger.map((r, i) => {
          const isUndone = undone.has(i);
          return (
            <div
              key={i}
              className={`rounded-xl border border-[var(--hairline)] px-4 py-3 transition ${
                isUndone ? "opacity-40" : "bg-white/[0.02]"
              }`}
            >
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <span className="tnum flex h-7 min-w-7 items-center justify-center rounded-md bg-indigo-glow/15 px-1.5 text-[12px] font-semibold text-indigo-soft">
                    {r.cells_affected}
                  </span>
                  <div>
                    <div className="text-[13.5px] text-ink">{r.rule}</div>
                    <div className="text-[11px] text-graphite">
                      {r.table} · {r.column}
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => toggleUndo(i)}
                  className="rounded-full border border-[var(--hairline)] px-3 py-1 text-[12px] text-graphite transition hover:text-ink"
                >
                  {isUndone ? "Redo" : "Undo"}
                </button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                <span className="rounded bg-white/5 px-2 py-1 text-graphite line-through decoration-red-400/50">
                  {r.before_sample.slice(0, 3).map(String).join("  ")}
                </span>
                <span className="text-graphite">→</span>
                <span className="rounded bg-indigo-glow/10 px-2 py-1 text-indigo-soft">
                  {r.after_sample
                    .slice(0, 3)
                    .map((v) => (v === null ? "NULL" : String(v)))
                    .join("  ")}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {/* duplicate / near-dup — keep/remove, never auto-delete */}
      {duplicates.length > 0 && (
      <h2 className="mt-8 text-[13px] uppercase tracking-wider text-graphite">
        Possible duplicates — your call
        <span className="ml-2 tnum text-graphite/70">({duplicates.length})</span>
      </h2>
      )}
      <div className="mt-3 space-y-2">
        {visibleDuplicates.map((d, i) => (
          <div
            key={i}
            className="flex flex-col gap-3 rounded-xl border border-[var(--hairline)] bg-white/[0.02] px-4 py-3 sm:flex-row sm:items-start sm:justify-between"
          >
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-3 text-[13px]">
                <Pill tone={d.kind === "near" ? "amber" : "muted"}>
                  {d.kind === "near" ? "Near" : "Exact"}
                </Pill>
                <span className="text-ink">{String(d.sample.name)}</span>
                <span className="tnum text-graphite">rows {d.row_indices.join(", ")}</span>
              </div>
              {/* one-line "why" for near-dups: which field differs */}
              {d.kind === "near" && (
                <p className="text-[12px] text-graphite">
                  {d.diff
                    ? `Differs in ${d.diff}`
                    : "Rows are similar but differ in a text field."}
                </p>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {(["keep", "remove"] as const).map((c) => (
                <button
                  key={c}
                  onClick={() => setDupChoice((s) => ({ ...s, [i]: c }))}
                  className={`rounded-full border px-3 py-1 text-[12px] transition ${
                    dupChoice[i] === c
                      ? c === "remove"
                        ? "border-red-400/40 bg-red-400/10 text-red-300"
                        : "border-indigo-glow/40 bg-indigo-glow/10 text-indigo-soft"
                      : "border-[var(--hairline)] text-graphite hover:text-ink"
                  }`}
                >
                  {c === "keep" ? "Keep both" : "Remove dupe"}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {duplicates.length > DUPE_CAP && (
        <button
          onClick={() => setShowAllDupes((v) => !v)}
          className="mt-3 text-[13px] text-indigo-soft transition hover:text-ink"
        >
          {showAllDupes ? "Show fewer" : `View ${hiddenDupeCount} more`}
        </button>
      )}

      <button
        onClick={onNext}
        className="mt-8 w-full rounded-full bg-indigo-glow py-3 text-[15px] font-medium text-white shadow-glow transition hover:brightness-110"
      >
        Confirm schema →
      </button>
    </div>
  );
}
