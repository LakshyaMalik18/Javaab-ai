"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { useAppData } from "./AppStore";

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xls,.json";

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UploadStep({ onNext }: { onNext: () => void }) {
  const { uploadFiles, uploading, sessionStatus } = useAppData();
  const [over, setOver] = useState(false);
  const [files, setFiles] = useState<File[]>([]);

  // one shared handler for both the file picker (browse) and drag-and-drop.
  // NOTE: staging is MIME-agnostic on purpose — macOS reports CSVs as
  // application/vnd.ms-excel or "" so we never reject on MIME. The backend
  // validates by extension (415 on unsupported types).
  const addFiles = (list: FileList | null) => {
    const picked = list ? Array.from(list) : [];
    console.log(
      "[upload] addFiles called with",
      picked.length,
      "file(s):",
      picked.map((f) => `${f.name} (${f.type || "no-mime"})`),
    );
    if (!picked.length) return;
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => f.name + f.size));
      const next = [...prev];
      for (const f of picked) {
        if (!seen.has(f.name + f.size)) next.push(f);
      }
      console.log("[upload] staged files now:", next.map((f) => f.name));
      return next;
    });
  };

  const submit = async () => {
    if (!files.length || uploading) return;
    const ok = await uploadFiles(files);
    if (ok) onNext();
  };

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="display text-[clamp(1.8rem,4vw,2.8rem)] text-ink">
        Drop your data in.
      </h1>
      <p className="mt-3 text-[15px] text-graphite">
        CSV, Excel or JSON — one file or several. Javaab cleans and connects them
        for you. Nothing is written to disk.
      </p>

      {/* the whole dropzone is a <label> wrapping the input, so a click anywhere
          opens the native picker — no JS .click() needed — and the same
          addFiles() handler runs for both the picker and drag-and-drop.
          The input is associated by NESTING only — no htmlFor — otherwise the
          label double-activates the input and the picker opens then cancels. */}
      <motion.label
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          addFiles(e.dataTransfer.files);
        }}
        animate={{
          borderColor: over ? "rgba(232,179,57,0.6)" : "rgba(244,244,242,0.12)",
          backgroundColor: over ? "rgba(232,179,57,0.06)" : "rgba(244,244,242,0.02)",
        }}
        className="mt-8 flex cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed py-16 text-center transition"
      >
        <input
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => {
            console.log("[upload] input onChange fired, files:", e.target.files);
            addFiles(e.target.files);
            e.target.value = ""; // allow re-selecting the same file
          }}
        />
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-indigo-glow/15 text-indigo-soft shadow-glow">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <path
              d="M12 16V4m0 0L7 9m5-5l5 5M5 20h14"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <p className="mt-4 text-[15px] text-ink">
          Drag files here, or <span className="accent">browse</span>
        </p>
        <p className="mt-1 text-[12px] text-graphite">
          CSV · Excel · JSON — up to several files at once
        </p>
      </motion.label>

      {/* v2 teaser */}
      <p className="mt-3 text-center text-[12px] text-graphite/70">
        Coming soon: connect SQL Server, Snowflake &amp; cloud storage.
      </p>

      {files.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-6 space-y-2"
        >
          {files.map((f) => (
            <div
              key={f.name + f.size}
              className="flex items-center justify-between rounded-xl border border-[var(--hairline)] bg-white/[0.02] px-4 py-3"
            >
              <div className="flex items-center gap-3">
                <span className="flex h-8 w-8 items-center justify-center rounded-md bg-indigo-glow/15 text-[11px] font-semibold text-indigo-soft">
                  {f.name.split(".").pop()?.toUpperCase()}
                </span>
                <div>
                  <div className="text-[13px] text-ink">{f.name}</div>
                  <div className="tnum text-[11px] text-graphite">{fmtSize(f.size)}</div>
                </div>
              </div>
              {uploading ? (
                <span className="text-[12px] text-graphite">analyzing…</span>
              ) : (
                <button
                  onClick={() => setFiles((p) => p.filter((x) => x !== f))}
                  className="text-[12px] text-graphite transition hover:text-ink"
                >
                  remove
                </button>
              )}
            </div>
          ))}
          <button
            onClick={submit}
            disabled={uploading || sessionStatus !== "ready"}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-full bg-indigo-glow py-3 text-[15px] font-medium text-white shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {uploading ? (
              <>
                <Spinner />
                Cleaning &amp; analyzing…
              </>
            ) : sessionStatus !== "ready" ? (
              "Starting session…"
            ) : (
              "Clean & analyze →"
            )}
          </button>
        </motion.div>
      )}
    </div>
  );
}

function Spinner() {
  return (
    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
  );
}
