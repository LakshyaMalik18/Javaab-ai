"use client";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import CleanSetPiece from "@/components/setpieces/CleanSetPiece";
import { Pill } from "@/components/ui";
import { useAppData } from "./AppStore";
import { useToast } from "./Toaster";

const TYPE_LABELS: { value: import("@/lib/api").CleaningRuleType; label: string }[] = [
  { value: "null_token", label: "Treat value as null" },
  { value: "force_type", label: "Force column type" },
  { value: "merge_values", label: "Merge category values" },
];
const DTYPES = ["numeric", "date", "boolean", "text"];

export default function CleaningStep({ onNext }: { onNext: () => void }) {
  const { report, resolveDuplicates, applyRules } = useAppData();
  const { toast } = useToast();
  const [addOpen, setAddOpen] = useState(false);
  const [dupChoice, setDupChoice] = useState<Record<number, "keep" | "remove">>({});
  const [showAllDupes, setShowAllDupes] = useState(false);
  const [applying, setApplying] = useState(false);

  // Add-rule form state (v1: three rule types)
  const [ruleType, setRuleType] =
    useState<import("@/lib/api").CleaningRuleType>("null_token");
  const [ruleColumn, setRuleColumn] = useState("");
  const [ruleValue, setRuleValue] = useState(""); // null token, or comma-sep merge sources
  const [ruleDtype, setRuleDtype] = useState("numeric");
  const [ruleTo, setRuleTo] = useState(""); // merge target label
  const [ruleBusy, setRuleBusy] = useState(false);

  const ledger = report?.ledger ?? [];
  const duplicates = report?.duplicates ?? [];
  const ambiguity = report?.ambiguity ?? [];

  // Near-dup lists can be long on transactional data; show a capped window with a
  // "view more" rather than dumping every pair into a minute-long scroll.
  const DUPE_CAP = 8;
  const visibleDuplicates = showAllDupes ? duplicates : duplicates.slice(0, DUPE_CAP);
  const hiddenDupeCount = duplicates.length - visibleDuplicates.length;

  // Build a typed rule from the form, apply it (re-runs cleaning on the raw data),
  // then refresh local state — the dup/undo choices are stale after a re-clean.
  const addRule = async () => {
    if (ruleBusy) return;
    const column = ruleColumn.trim();
    if (!column) {
      toast("Enter the column the rule applies to.");
      return;
    }
    let rule: import("@/lib/api").CleaningRule;
    if (ruleType === "null_token") {
      if (!ruleValue.trim()) {
        toast("Enter the value to treat as null.");
        return;
      }
      rule = { type: "null_token", column, params: { value: ruleValue.trim() } };
    } else if (ruleType === "force_type") {
      rule = { type: "force_type", column, params: { dtype: ruleDtype } };
    } else {
      const from = ruleValue
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (!from.length || !ruleTo.trim()) {
        toast("Enter the values to merge and the label to merge them into.");
        return;
      }
      rule = { type: "merge_values", column, params: { from, to: ruleTo.trim() } };
    }

    setRuleBusy(true);
    const ok = await applyRules([rule]);
    setRuleBusy(false);
    if (!ok) return; // error already toasted — keep the form open to fix it

    // a re-clean rebuilt the tables → prior dup selections no longer line up
    setDupChoice({});
    setRuleValue("");
    setRuleTo("");
    setRuleColumn("");
    setAddOpen(false);
    toast("Rule applied — cleaning re-ran on your data.", "info");
  };

  // On proceeding, apply the user's explicit duplicate decisions to the real data
  // (removal only happens for groups marked "remove"), then advance. Anything left
  // un-chosen or marked "keep" is untouched — nothing is auto-removed.
  const proceed = async () => {
    if (applying) return;
    const decisions = Object.entries(dupChoice)
      .filter(([, action]) => action === "remove")
      .map(([i, action]) => {
        const d = duplicates[Number(i)];
        return {
          table: String(d.sample.name),
          row_indices: d.row_indices,
          action,
        };
      });

    if (decisions.length === 0) {
      onNext();
      return;
    }

    setApplying(true);
    const removed = await resolveDuplicates(decisions);
    setApplying(false);
    if (removed === null) return; // error already toasted — let the user retry
    if (removed > 0) {
      toast(
        `Removed ${removed} duplicate row${removed === 1 ? "" : "s"}.`,
        "info",
      );
    }
    onNext();
  };

  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-6">
        <h1 className="display text-[clamp(2.2rem,5vw,3.4rem)] text-ink">
          Here&apos;s what I cleaned.
        </h1>
        <p className="mt-2 text-[15px] text-graphite">
          Every change is listed with before/after samples. Add your own cleaning
          rule and Javaab re-runs the engine on your data.
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
            <div className="flex flex-col gap-3 rounded-xl border border-[var(--hairline)] bg-white/[0.02] p-4 sm:flex-row sm:flex-wrap sm:items-center">
              <select
                value={ruleType}
                onChange={(e) =>
                  setRuleType(e.target.value as import("@/lib/api").CleaningRuleType)
                }
                className="rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-3 py-2 text-[13px] text-ink"
              >
                {TYPE_LABELS.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
              <input
                value={ruleColumn}
                onChange={(e) => setRuleColumn(e.target.value)}
                placeholder="column"
                className="rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-3 py-2 text-[13px] text-ink placeholder:text-graphite/60"
              />

              {ruleType === "force_type" ? (
                <select
                  value={ruleDtype}
                  onChange={(e) => setRuleDtype(e.target.value)}
                  className="rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-3 py-2 text-[13px] text-ink"
                >
                  {DTYPES.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  value={ruleValue}
                  onChange={(e) => setRuleValue(e.target.value)}
                  placeholder={
                    ruleType === "null_token"
                      ? "value to null (e.g. 9999)"
                      : "values to merge (comma-separated)"
                  }
                  className="flex-1 rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-3 py-2 text-[13px] text-ink placeholder:text-graphite/60"
                />
              )}

              {ruleType === "merge_values" && (
                <input
                  value={ruleTo}
                  onChange={(e) => setRuleTo(e.target.value)}
                  placeholder="merge into (canonical label)"
                  className="flex-1 rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-3 py-2 text-[13px] text-ink placeholder:text-graphite/60"
                />
              )}

              <button
                onClick={addRule}
                disabled={ruleBusy}
                className="rounded-lg bg-indigo-glow px-3 py-2 text-[13px] font-medium text-white transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {ruleBusy ? "Re-running…" : "Re-run cleaning"}
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
          return (
            <div
              key={i}
              className="rounded-xl border border-[var(--hairline)] bg-white/[0.02] px-4 py-3 transition"
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
        onClick={proceed}
        disabled={applying}
        className="mt-8 w-full rounded-full bg-indigo-glow py-3 text-[15px] font-medium text-white shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {applying ? "Removing duplicates…" : "Confirm schema →"}
      </button>
    </div>
  );
}
