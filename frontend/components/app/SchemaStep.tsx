"use client";
import { useEffect, useState } from "react";
import type { ColumnContract } from "@/lib/types";
import { ConfidenceMeter, Pill } from "@/components/ui";
import { useAppData } from "./AppStore";
import type { ColumnEdit, DataDictionaryEntry } from "@/lib/api";

const TYPES = ["numeric", "date", "boolean", "text"];
const ROLES = ["id", "dimension", "measure", "timestamp", "text"];

type EditMap = Record<string, ColumnEdit>;
type DescMap = Record<string, string>;

export default function SchemaStep({ onNext }: { onNext: () => void }) {
  const { schema, schemaLoading, schemaError, loadSchema, confirmSchema } =
    useAppData();
  const [edits, setEdits] = useState<EditMap>({});
  const [descs, setDescs] = useState<DescMap>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadSchema();
  }, [loadSchema]);

  const setEdit = (table: string, column: string, patch: Partial<ColumnEdit>) =>
    setEdits((m) => {
      const key = `${table}.${column}`;
      return { ...m, [key]: { ...m[key], ...patch, table, column } };
    });

  const setDesc = (table: string, column: string, description: string) =>
    setDescs((m) => ({ ...m, [`${table}.${column}`]: description }));

  const proceed = async () => {
    console.log("[SchemaStep] Confirm clicked", {
      schemaLoaded: !!schema,
      saving,
      edits: Object.keys(edits).length,
      descs: Object.keys(descs).length,
    });
    const column_edits = Object.values(edits);
    const data_dictionary: DataDictionaryEntry[] = Object.entries(descs)
      .filter(([, v]) => v.trim())
      .map(([key, description]) => {
        const [table, ...rest] = key.split(".");
        return { table, column: rest.join("."), description: description.trim() };
      });

    if (column_edits.length || data_dictionary.length) {
      setSaving(true);
      const ok = await confirmSchema(column_edits, data_dictionary);
      setSaving(false);
      if (!ok) {
        console.warn("[SchemaStep] confirmSchema failed — not advancing");
        return;
      }
    }
    console.log("[SchemaStep] advancing to relationships");
    onNext();
  };

  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-6">
        <h1 className="display text-[clamp(2.2rem,5vw,3.4rem)] text-ink">
          Confirm what it means.
        </h1>
        <p className="mt-2 text-[15px] text-graphite">
          Edit any type or meaning. Low-confidence columns glow — answer its
          question, or type a description to tell Javaab what the column means.
        </p>
      </header>

      {schemaLoading && !schema && <SchemaSkeleton />}

      {schemaError && !schema && (
        <div className="rounded-xl border border-red-400/25 bg-red-400/[0.06] px-4 py-4 text-[13px] text-red-100">
          {schemaError}
          <button
            onClick={() => loadSchema(true)}
            className="ml-3 rounded-full border border-[var(--hairline)] px-3 py-1 text-[12px] text-graphite transition hover:text-ink"
          >
            Retry
          </button>
        </div>
      )}

      {schema && (
        <div className="space-y-8">
          {schema.tables.map((t) => (
            <div key={t.name}>
              <div className="mb-3 flex items-center gap-3">
                <h2 className="display text-[20px] text-ink">{t.name}</h2>
                <span className="tnum text-[12px] text-graphite">
                  {t.row_count.toLocaleString()} rows
                </span>
                <span className="text-[12px] text-graphite">· {t.summary}</span>
              </div>
              <div className="overflow-hidden rounded-xl border border-[var(--hairline)]">
                <div className="grid grid-cols-[1fr_1fr_2fr_auto] gap-2 border-b border-[var(--hairline)] bg-white/[0.02] px-4 py-2 text-[11px] uppercase tracking-wider text-graphite">
                  <span>Column</span>
                  <span>Type / role</span>
                  <span>Meaning</span>
                  <span>Confidence</span>
                </div>
                {t.columns.map((c) => (
                  <SchemaRow
                    key={c.name}
                    table={t.name}
                    col={c}
                    onEdit={(patch) => setEdit(t.name, c.name, patch)}
                    onDesc={(d) => setDesc(t.name, c.name, d)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <button
        onClick={proceed}
        disabled={!schema || saving}
        className="mt-8 w-full rounded-full bg-indigo-glow py-3 text-[15px] font-medium text-white shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {saving ? "Saving…" : "See relationships →"}
      </button>
    </div>
  );
}

function SchemaSkeleton() {
  return (
    <div className="space-y-3">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="h-14 animate-pulse rounded-xl border border-[var(--hairline)] bg-white/[0.02]"
        />
      ))}
      <p className="pt-1 text-[12px] text-graphite">
        Reading your columns and labelling their meaning…
      </p>
    </div>
  );
}

function SchemaRow({
  table,
  col,
  onEdit,
  onDesc,
}: {
  table: string;
  col: ColumnContract;
  onEdit: (patch: Partial<ColumnEdit>) => void;
  onDesc: (d: string) => void;
}) {
  const [meaning, setMeaning] = useState(col.meaning);
  const [desc, setDescLocal] = useState("");
  const low = col.provisional;

  return (
    <div
      className={`grid grid-cols-[1fr_1fr_2fr_auto] items-center gap-2 border-t border-[var(--hairline)] px-4 py-3 ${
        low ? "bg-amber-warm/[0.05] shadow-[inset_2px_0_0_#F0C04A]" : ""
      }`}
    >
      <div>
        <div className="text-[13px] text-ink">{col.name}</div>
        <code className="text-[11px] text-graphite">{col.raw_name}</code>
      </div>
      <div className="flex flex-col gap-1">
        <select
          defaultValue={col.dtype}
          onChange={(e) => onEdit({ dtype: e.target.value })}
          className="rounded-md border border-[var(--hairline)] bg-obsidian-700 px-2 py-1 text-[12px] text-ink"
        >
          {TYPES.map((tp) => (
            <option key={tp}>{tp}</option>
          ))}
        </select>
        <select
          defaultValue={col.role}
          onChange={(e) => onEdit({ role: e.target.value })}
          className="rounded-md border border-[var(--hairline)] bg-obsidian-700 px-2 py-1 text-[12px] text-graphite"
        >
          {ROLES.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
      </div>
      <div>
        <input
          value={meaning}
          onChange={(e) => {
            setMeaning(e.target.value);
            onEdit({ meaning: e.target.value });
          }}
          className="w-full rounded-md border border-transparent bg-transparent px-1 py-1 text-[13px] text-ink hover:border-[var(--hairline)] focus:border-indigo-glow/50 focus:outline-none"
        />
        {low && (
          <div className="mt-1.5 space-y-1.5">
            <div className="flex items-center gap-2 text-[12px] text-amber-warm">
              <span>?</span>
              {col.clarifying_question}
            </div>
            <div className="flex gap-2">
              <input
                value={desc}
                onChange={(e) => {
                  setDescLocal(e.target.value);
                  onDesc(e.target.value);
                }}
                placeholder="describe what this column means…"
                className="flex-1 rounded-md border border-[var(--hairline)] bg-obsidian-700 px-2 py-1 text-[12px] text-ink placeholder:text-graphite/60"
              />
            </div>
          </div>
        )}
      </div>
      <div className="flex flex-col items-end gap-1">
        <ConfidenceMeter value={col.confidence} />
        {col.is_id && <Pill tone="muted">{col.is_fk ? "FK" : "PK"}</Pill>}
        {low && <Pill tone="amber">provisional</Pill>}
      </div>
    </div>
  );
}
