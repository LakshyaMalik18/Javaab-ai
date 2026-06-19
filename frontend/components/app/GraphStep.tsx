"use client";
import { useEffect, useMemo, useState } from "react";
import JoinDrawSetPiece from "@/components/setpieces/JoinDrawSetPiece";
import { Pill } from "@/components/ui";
import type { RelationshipChoice } from "@/lib/api";
import type { RelationshipEdge } from "@/lib/types";
import { useAppData } from "./AppStore";

// A stable identity for an edge, and the undirected table-pair it belongs to.
const edgeId = (e: RelationshipEdge) =>
  `${e.from_table}.${e.from_col}->${e.to_table}.${e.to_col}`;
const pairKey = (e: RelationshipEdge) =>
  [e.from_table, e.to_table].slice().sort().join(" ~ ");

interface Pair {
  key: string;
  tables: [string, string];
  candidates: RelationshipEdge[];
}

export default function GraphStep({ onNext }: { onNext: () => void }) {
  const { schema, confirmSchema } = useAppData();
  const discovered = useMemo(() => schema?.relationships ?? [], [schema]);
  // Manually-defined joins live in local state until the user confirms; on confirm,
  // any that are the active link for their pair are persisted to the backend (see
  // confirmAndNext) so they become real, query-time relationships.
  const [manual, setManual] = useState<RelationshipEdge[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  // Informational FYI shown every time a manual join is added — manual joins are
  // trusted as-is and never semantically checked (purely informational; the join
  // is still added normally).
  const [manualNotice, setManualNotice] = useState(false);

  const rels = useMemo(() => [...discovered, ...manual], [discovered, manual]);
  const tables = schema?.tables ?? [];

  // Group every edge by its undirected table-pair: ONE card / selector per pair.
  const pairs = useMemo<Pair[]>(() => {
    const m = new Map<string, Pair>();
    for (const e of rels) {
      const k = pairKey(e);
      const cur = m.get(k);
      if (cur) cur.candidates.push(e);
      else
        m.set(k, {
          key: k,
          tables: [e.from_table, e.to_table],
          candidates: [e],
        });
    }
    // strongest candidate first inside each pair
    for (const p of m.values())
      p.candidates.sort((a, b) => b.confidence - a.confidence);
    return [...m.values()];
  }, [rels]);

  // active edge per pair: user choice (edgeId) overriding the backend default.
  const defaultActiveId = (p: Pair) =>
    edgeId(p.candidates.find((c) => c.active) ?? p.candidates[0]);
  const [active, setActive] = useState<Record<string, string>>({});

  // seed defaults from the backend's per-pair active flags whenever they change
  useEffect(() => {
    setActive((prev) => {
      const next = { ...prev };
      for (const p of pairs) if (!(p.key in next)) next[p.key] = defaultActiveId(p);
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairs]);

  const activeEdge = (p: Pair): RelationshipEdge => {
    const id = active[p.key] ?? defaultActiveId(p);
    return p.candidates.find((c) => edgeId(c) === id) ?? p.candidates[0];
  };

  const choose = (pairK: string, id: string) =>
    setActive((s) => ({ ...s, [pairK]: id }));

  // The node diagram is only legible for a handful of tables. Cap it: render the
  // real-schema diagram cards when there are 4 or fewer tables AND a pair to draw;
  // for 5+ tables render the SAME one-selector-per-pair as a compact list instead.
  const showDiagram = tables.length > 0 && tables.length <= 4 && pairs.length > 0;

  const toggleManual = () => setShowForm((v) => !v);
  const addManual = (edge: RelationshipEdge) => {
    setManual((m) => [...m, edge]);
    // a freshly added link becomes the active choice for its pair
    setActive((s) => ({ ...s, [pairKey(edge)]: edgeId(edge) }));
    setShowForm(false);
    // surface the trust caveat every time a manual join is added (informational only)
    setManualNotice(true);
  };

  const confirmAndNext = async () => {
    // Persist exactly one active link per pair. A discovered active edge is sent as a
    // relationship_choice; a user-defined active edge is sent as a manual_relationship
    // — the backend validates it, persists it, and makes it the pair's active link, so
    // it flows into the same machinery (nl2sql + join-path + guardrail) as a discovered
    // join. An invalid manual join is rejected server-side and we stay on this step.
    const discoveredIds = new Set(discovered.map(edgeId));
    const toChoice = (e: RelationshipEdge): RelationshipChoice => ({
      from_table: e.from_table,
      from_col: e.from_col,
      to_table: e.to_table,
      to_col: e.to_col,
    });
    const activeEdges = pairs.map((p) => activeEdge(p));
    const choices = activeEdges.filter((e) => discoveredIds.has(edgeId(e))).map(toChoice);
    const manualRels = activeEdges
      .filter((e) => !discoveredIds.has(edgeId(e)))
      .map(toChoice);

    setSaving(true);
    let ok = true;
    try {
      if (choices.length || manualRels.length)
        ok = await confirmSchema([], [], choices, manualRels);
    } finally {
      setSaving(false);
      if (ok) onNext();
    }
  };

  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-6">
        <h1 className="display text-[clamp(2.2rem,5vw,3.4rem)] text-ink">
          How your files connect.
        </h1>
        <p className="mt-2 text-[15px] text-graphite">
          Discovered by matching values, not just names. For each pair of files,
          pick the one link to join on — only the active link is used in every query.
        </p>
      </header>

      <div className="space-y-3">
        {pairs.length === 0 && (
          <p className="rounded-xl border border-[var(--hairline)] bg-white/[0.02] px-4 py-3 text-[13px] text-graphite">
            No relationships were discovered between your files — that&apos;s
            expected for a single table. You can define a join manually below.
          </p>
        )}

        {pairs.map((p) => {
          const sel = activeEdge(p);
          const diagram = showDiagram ? buildDiagramProps(sel, tables) : null;
          return (
            <div
              key={p.key}
              className="glass-strong rounded-2xl p-4 shadow-glass sm:p-5"
            >
              <div className="mb-3 flex items-center gap-2 text-[13px] text-graphite">
                <code className="text-indigo-soft">{p.tables[0]}</code>
                <span>⇄</span>
                <code className="text-indigo-soft">{p.tables[1]}</code>
              </div>

              {diagram && (
                <div className="mb-3">
                  <JoinDrawSetPiece
                    edge={diagram.edge}
                    leftTable={diagram.leftTable}
                    rightTable={diagram.rightTable}
                    leftCols={diagram.leftCols}
                    rightCols={diagram.rightCols}
                    fkRow={diagram.fkRow}
                    pkRow={diagram.pkRow}
                  />
                </div>
              )}

              <PairSelector
                pair={p}
                selectedId={edgeId(sel)}
                onChange={(id) => choose(p.key, id)}
              />
            </div>
          );
        })}

        {showForm && (
          <ManualJoinForm
            schema={schema}
            onAdd={addManual}
            onCancel={() => setShowForm(false)}
          />
        )}

        {manualNotice && (
          <div className="flex items-start gap-3 rounded-xl border border-amber-warm/30 bg-amber-warm/10 px-4 py-3 text-[13px] text-amber-warm">
            <span aria-hidden className="mt-px select-none">⚠</span>
            <p className="leading-relaxed">
              Manual join added. Heads up — Javaab trusts manual joins as-is and
              doesn&apos;t check whether the two columns are meaningful to join. If
              you&apos;ve linked unrelated columns, results may be wrong without
              warning.
            </p>
            <button
              onClick={() => setManualNotice(false)}
              aria-label="Dismiss note"
              className="ml-auto shrink-0 text-amber-warm/70 transition hover:text-amber-warm"
            >
              ✕
            </button>
          </div>
        )}

        <button
          onClick={toggleManual}
          className="w-full rounded-xl border border-dashed border-[var(--hairline)] py-3 text-[13px] text-graphite transition hover:text-ink"
        >
          {showForm ? "Close" : "+ Define a join manually"}
        </button>
      </div>

      <button
        onClick={confirmAndNext}
        disabled={saving}
        className="mt-8 w-full rounded-full bg-indigo-glow py-3 text-[15px] font-medium text-white shadow-glow transition hover:brightness-110 disabled:opacity-60"
      >
        {saving ? "Saving links…" : "Start asking questions →"}
      </button>
    </div>
  );
}

// One selector per pair: a single control whose options are the pair's candidate
// links, the active one selected. Choosing an alternative switches the active link
// (there is no multi-select — a pair can only ever have one active link).
function PairSelector({
  pair,
  selectedId,
  onChange,
}: {
  pair: Pair;
  selectedId: string;
  onChange: (id: string) => void;
}) {
  const sel = pair.candidates.find((c) => edgeId(c) === selectedId);
  const single = pair.candidates.length === 1;
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-2 text-[12px] uppercase tracking-wider text-graphite">
        Active link
        {sel && (
          <Pill tone={sel.confidence_label === "high" ? "indigo" : "amber"}>
            {sel.confidence_label} · {Math.round(sel.confidence * 100)}%
          </Pill>
        )}
      </div>
      <select
        value={selectedId}
        onChange={(e) => onChange(e.target.value)}
        disabled={single}
        className="rounded-md border border-[var(--hairline)] bg-obsidian-700 px-3 py-1.5 text-[13px] text-ink disabled:opacity-70"
      >
        {pair.candidates.map((c) => (
          <option key={edgeId(c)} value={edgeId(c)}>
            {c.from_table}.{c.from_col} → {c.to_table}.{c.to_col} (
            {c.confidence_label} · {Math.round(c.confidence * 100)}%)
          </option>
        ))}
      </select>
    </div>
  );
}

// Keep a table card readable: cap the column list, always keeping the join key.
function capCols(cols: string[], keyCol: string, max = 7): string[] {
  if (cols.length <= max) return cols;
  const head = cols.slice(0, max);
  if (!head.includes(keyCol)) head[max - 1] = keyCol;
  return head;
}

// Translate a real RelationshipEdge + the session schema into the props the
// JoinDrawSetPiece needs — using ACTUAL table/column names, never the mock.
function buildDiagramProps(
  edge: RelationshipEdge,
  tables: { name: string; columns: { name: string }[] }[],
) {
  const lt = tables.find((t) => t.name === edge.from_table);
  const rt = tables.find((t) => t.name === edge.to_table);
  if (!lt || !rt) return null; // edge references a table we don't have — skip it
  const leftCols = capCols(lt.columns.map((c) => c.name), edge.from_col);
  const rightCols = capCols(rt.columns.map((c) => c.name), edge.to_col);
  const fkRow = Math.max(0, leftCols.indexOf(edge.from_col));
  const pkRow = Math.max(0, rightCols.indexOf(edge.to_col));
  return {
    edge,
    leftTable: edge.from_table,
    rightTable: edge.to_table,
    leftCols,
    rightCols,
    fkRow,
    pkRow,
  };
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

  const valid = fromTable && fromCol && toTable && toCol && fromTable !== toTable;

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
      active: true,
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
