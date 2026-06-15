"use client";
import { motion } from "framer-motion";

export type StepId = "upload" | "cleaning" | "schema" | "graph" | "chat";

export const STEPS: { id: StepId; label: string }[] = [
  { id: "upload", label: "Upload" },
  { id: "cleaning", label: "Cleaning" },
  { id: "schema", label: "Schema" },
  { id: "graph", label: "Relationships" },
  { id: "chat", label: "Ask" },
];

export default function StepRail({
  active,
  reached,
  onGo,
}: {
  active: StepId;
  reached: Set<StepId>;
  onGo: (s: StepId) => void;
}) {
  return (
    <nav className="flex flex-col gap-0.5">
      {STEPS.map((s, i) => {
        const isActive = s.id === active;
        const canGo = reached.has(s.id);
        return (
          <button
            key={s.id}
            disabled={!canGo}
            onClick={() => canGo && onGo(s.id)}
            className={`group relative flex items-center gap-3 py-2 pl-4 text-left text-[12.5px] transition ${
              isActive
                ? "text-ink"
                : canGo
                  ? "text-graphite hover:text-ink-dim"
                  : "cursor-not-allowed text-graphite/30"
            }`}
          >
            <span
              className={`tnum text-[10.5px] ${
                isActive ? "text-indigo-soft" : "text-graphite/50"
              }`}
            >
              0{i + 1}
            </span>
            {s.label}
            {isActive && (
              <motion.span
                layoutId="rail-dot"
                className="absolute left-0 h-4 w-px rounded-full bg-indigo-glow shadow-glow"
              />
            )}
          </button>
        );
      })}
    </nav>
  );
}
