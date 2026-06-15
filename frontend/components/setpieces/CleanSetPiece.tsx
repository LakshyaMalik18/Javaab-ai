"use client";
// SET-PIECE 1 — "The Clean". The mess resolves IN PLACE: currency strips to
// numbers, dates snap to ISO, USA/America merge into one, dupes/junk fade, and a
// counter ticks "N cells cleaned". Reusable: marketing feeds it mock rows,
// /app feeds it the real ingest result of the same shape.
import { useEffect, useRef, useState } from "react";
import { motion, useInView, animate } from "framer-motion";
import { RAW_ROWS, CLEAN_ROWS, MOCK_CELLS_CLEANED } from "@/lib/mock";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";

type Rows = (string | number | null)[][];

export default function CleanSetPiece({
  raw = RAW_ROWS,
  clean = CLEAN_ROWS,
  cellsCleaned = MOCK_CELLS_CLEANED,
  auto = true,
}: {
  raw?: Rows;
  clean?: Rows;
  cellsCleaned?: number;
  auto?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-120px" });
  const reduced = usePrefersReducedMotion();
  const [resolved, setResolved] = useState(false);
  const [count, setCount] = useState(0);

  const start = auto && inView;

  useEffect(() => {
    if (!start) return;
    const t = setTimeout(() => setResolved(true), reduced ? 0 : 700);
    return () => clearTimeout(t);
  }, [start, reduced]);

  useEffect(() => {
    if (!resolved) return;
    const controls = animate(0, cellsCleaned, {
      duration: reduced ? 0 : 1.4,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setCount(Math.round(v)),
    });
    return () => controls.stop();
  }, [resolved, cellsCleaned, reduced]);

  const header = (resolved ? clean : raw)[0] as string[];
  const body = (resolved ? clean : raw).slice(1);
  // raw has extra junk rows (blank + Total) that disappear after cleaning
  const rawBody = raw.slice(1);

  return (
    <div ref={ref} className="w-full">
      <div className="glass-strong overflow-hidden rounded-2xl shadow-glass">
        <div className="flex items-center justify-between border-b border-[var(--hairline)] px-5 py-3">
          <div className="flex items-center gap-2 text-[13px] text-graphite">
            <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
            <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
            <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
            <span className="ml-3 font-medium">orders_export.csv</span>
          </div>
          <motion.div
            className="tnum text-[13px] font-medium"
            animate={{ color: resolved ? "#F0C04A" : "#8A8A93" }}
          >
            {resolved ? (
              <span className="answer">{count} cells cleaned</span>
            ) : (
              <span>raw · messy</span>
            )}
          </motion.div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-[13px]">
            <thead>
              <tr className="text-graphite">
                {header.map((h, i) => (
                  <th
                    key={i}
                    className="whitespace-nowrap px-4 py-2.5 font-medium"
                  >
                    <motion.span
                      key={String(h)}
                      initial={false}
                      animate={{ opacity: 1 }}
                      className={resolved ? "text-indigo-soft" : ""}
                    >
                      {h}
                    </motion.span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(resolved ? body : rawBody).map((row, r) => {
                // junk rows (last two in raw) collapse away when resolved
                const isJunk =
                  !resolved && r >= body.length; // blank + Total rows
                return (
                  <motion.tr
                    key={r}
                    className="border-t border-[var(--hairline)]"
                    initial={false}
                    animate={{
                      opacity: isJunk ? 0.35 : 1,
                    }}
                  >
                    {row.map((cell, c) => (
                      <Cell key={c} value={cell} resolved={resolved} junk={isJunk} />
                    ))}
                  </motion.tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* tiny legend of what just happened */}
      <motion.div
        className="mt-4 flex flex-wrap gap-2 text-[11px] text-graphite"
        initial={{ opacity: 0 }}
        animate={{ opacity: resolved ? 1 : 0 }}
        transition={{ delay: 0.3 }}
      >
        {[
          "$1,240 → 1240",
          "13/04/2024 → 2024-04-13",
          "USA · America → United States",
          "NA · - → NULL",
          "junk rows dropped",
        ].map((t) => (
          <span
            key={t}
            className="rounded-md border border-[var(--hairline)] bg-white/5 px-2 py-1"
          >
            {t}
          </span>
        ))}
      </motion.div>
    </div>
  );
}

function Cell({
  value,
  resolved,
  junk,
}: {
  value: string | number | null;
  resolved: boolean;
  junk: boolean;
}) {
  const isNull = value === null;
  const display = isNull ? "NULL" : String(value);
  return (
    <td className="whitespace-nowrap px-4 py-2.5">
      <motion.span
        layout
        initial={false}
        animate={{
          color: junk
            ? "#5a5a60"
            : isNull
              ? "#5a5a60"
              : resolved
                ? "#F4F4F2"
                : "#b9b9be",
        }}
        className={`tnum ${isNull ? "italic" : ""}`}
      >
        {display}
      </motion.span>
    </td>
  );
}
