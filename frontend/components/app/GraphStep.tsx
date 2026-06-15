"use client";
import { useMemo, useState } from "react";
import JoinDrawSetPiece from "@/components/setpieces/JoinDrawSetPiece";
import { Pill } from "@/components/ui";
import type { RelationshipEdge } from "@/lib/types";
import { useAppData } from "./AppStore";

export default function GraphStep({ onNext }: { onNext: () => void }) {
  const { schema } = useAppData();
  const discovered = schema?.relationships ?? [];
  // Manually-defined joins live in local state — there is no backend persistence
  // endpoint for relationships yet, so they are surfaced here for confirmation.
  const [manual, setManual] = useState<RelationshipEdge[]>([]);
  const [confirmed, setConfirmed] = useState<Set<number>>(new Set());
  const [showForm, setShowForm] = useState(false);

  const rels = useMemo(() => [...discovered, ...manual], [discovered, manual]);

  const confirmRel = (i: number) => {
    console.log("[GraphStep] Confirm relationship", i, rels[i]);
    setConfirmed((s) => new Set(s).add(i));
  };

  const toggleManual = () => {
    console.log("[GraphStep] Define a join manually clicked", { showForm });
    setShowForm((v) => !v);
  };

  const addManual = (edge: RelationshipEdge) => {
    console.log("[GraphStep] manual join added", edge);
    setManual((m) => [...m, edge]);
    setShowForm(false);
  };

  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-6">
        <h1 className="display text-[clamp(2.2rem,5vw,3.4rem)] text-ink">
          How your files connect.
        </h1>
        <p className="mt-2 text-[15px] text-graphite">
          Discovered by matching values, not just names. Confirm, edit, or add a
          link — these keys are fed verbatim into every JOIN.
        </p>
      </header>

      <div className="glass-strong rounded-2xl p-4 shadow-glass sm:p-6">
        <JoinDrawSetPiece />
      </div>

      <div className="mt-6 space-y-2">
        {rels.length === 0 && (
          <p className="rounded-xl border border-[var(--hairline)] bg-white/[0.02] px-4 py-3 text-[13px] text-graphite">
            No relationships were discovered between your files — that&apos;s
            expected for a single table. You can define a join manually below.
          </p>
        )}
        {rels.map((r, i) => (
          <div
            key={i}
            className="flex flex-col gap-3 rounded-xl border border-[var(--hairline)] bg-white/[0.02] px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <Pill tone={r.confidence_label === "high" ? "indigo" : "amber"}>
                {r.confidence_label} · {Math.round(r.confidence * 100)}%
              </Pill>
              <code className="text-indigo-soft">
                {r.from_table}.{r.from_col}
              </code>
              <span className="text-graphite">→</span>
              <code className="text-amber-warm">
                {r.to_table}.{r.to_col}
              </code>
              <span className="text-[11px] text-graphite">many-to-one</span>
            </div>
            <div className="flex items-center gap-2">
              {confirmed.has(i) ? (
                <span className="rounded-full bg-indigo-glow/15 px-3 py-1 text-[12px] text-indigo-soft">
                  Confirmed ✓
                </span>
              ) : (
                <button
                  onClick={() => confirmRel(i)}
                  className="rounded-full bg-indigo-glow/15 px-3 py-1 text-[12px] text-indigo-soft transition hover:bg-indigo-glow/25"
                >
                  Confirm
                </button>
              )}
              <button
                onClick={toggleManual}
                className="rounded-full border border-[var(--hairline)] px-3 py-1 text-[12px] text-graphite transition hover:text-ink"
              >
                Edit
              </button>
            </div>
          </div>
        ))}

        {showForm && <ManualJoinForm schema={schema} onAdd={addManual} onCancel={() => setShowForm(false)} />}

        <button
          onClick={toggleManual}
          className="w-full rounded-xl border border-dashed border-[var(--hairline)] py-3 text-[13px] text-graphite transition hover:text-ink"
        >
          {showForm ? "Close" : "+ Define a join manually"}
        </button>
      </div>

      <button
        onClick={onNext}
        className="mt-8 w-full rounded-full bg-indigo-glow py-3 text-[15px] font-medium text-white shadow-glow transition hover:brightness-110"
      >
        Start asking questions →
      </button>
    </div>
  );
}

function ManualJoinForm({
  schema,
  onAdd,
  onCancel,
}: {
  schema: ReturnType<typeof useAppData>["schema"];
  onAdd: (edge: RelationshipEdge) => void;
  onCancel: () => void;
}) {
  const tables = schema?.tables ?? [];
  const [fromTable, setFromTable] = useState(tables[0]?.name ?? "");
  const [fromCol, setFromCol] = useState(tables[0]?.columns[0]?.name ?? "");
  const [toTable, setToTable] = useState(tables[1]?.name ?? tables[0]?.name ?? "");
  const [toCol, setToCol] = useState(
    (tables[1] ?? tables[0])?.columns[0]?.name ?? "",
  );

  const colsOf = (name: string) =>
    tables.find((t) => t.name === name)?.columns ?? [];

  const valid = fromTable && fromCol && toTable && toCol;

  const submit = () => {
    if (!valid) return;
    onAdd({
      from_table: fromTable,
      from_col: fromCol,
      to_table: toTable,
      to_col: toCol,
      confidence: 1,
      confidence_label: "high",
      provisional: false,
    });
  };

  return (
    <div className="rounded-xl border border-indigo-glow/30 bg-indigo-glow/[0.04] px-4 py-4">
      <div className="mb-3 text-[12px] uppercase tracking-wider text-graphite">
        Define a join
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <Select value={fromTable} onChange={(v) => { setFromTable(v); setFromCol(colsOf(v)[0]?.name ?? ""); }} options={tables.map((t) => t.name)} />
        <span className="text-graphite">.</span>
        <Select value={fromCol} onChange={setFromCol} options={colsOf(fromTable).map((c) => c.name)} />
        <span className="px-1 text-graphite">→</span>
        <Select value={toTable} onChange={(v) => { setToTable(v); setToCol(colsOf(v)[0]?.name ?? ""); }} options={tables.map((t) => t.name)} />
        <span className="text-graphite">.</span>
        <Select value={toCol} onChange={setToCol} options={colsOf(toTable).map((c) => c.name)} />
      </div>
      <div className="mt-3 flex gap-2">
        <button
          onClick={submit}
          disabled={!valid}
          className="rounded-full bg-indigo-glow px-4 py-1.5 text-[12px] font-medium text-white transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Add link
        </button>
        <button
          onClick={onCancel}
          className="rounded-full border border-[var(--hairline)] px-4 py-1.5 text-[12px] text-graphite transition hover:text-ink"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-[var(--hairline)] bg-obsidian-700 px-2 py-1 text-[12px] text-ink"
    >
      {options.map((o) => (
        <option key={o}>{o}</option>
      ))}
    </select>
  );
}
