"use client";
import { motion } from "framer-motion";
import { fadeUp } from "@/lib/motion";

export function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-[var(--hairline)] bg-white/[0.03] px-3.5 py-1.5 text-[12px] font-medium tracking-wide text-ink-dim backdrop-blur">
      <span className="h-1.5 w-1.5 rounded-full bg-indigo-glow shadow-glow" />
      {children}
    </span>
  );
}

export function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone =
    value >= 0.7 ? "#E8B339" : value >= 0.5 ? "#F0C04A" : "#FF6B6B";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-white/10">
        <motion.div
          className="h-full rounded-full"
          style={{ background: tone, boxShadow: `0 0 12px ${tone}80` }}
          initial={{ width: 0 }}
          whileInView={{ width: `${pct}%` }}
          viewport={{ once: true }}
          transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>
      <span className="tnum text-[11px] text-graphite">{pct}%</span>
    </div>
  );
}

export function Reveal({
  children,
  delay = 0,
  className = "",
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}) {
  return (
    <motion.div
      variants={fadeUp}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: "-80px" }}
      transition={{ delay }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

export function Pill({
  children,
  tone = "indigo",
}: {
  children: React.ReactNode;
  tone?: "indigo" | "amber" | "muted" | "red";
}) {
  const map = {
    indigo: "border-indigo-glow/30 bg-indigo-glow/10 text-indigo-soft",
    amber: "border-amber-warm/30 bg-amber-warm/10 text-amber-warm",
    muted: "border-[var(--hairline)] bg-white/5 text-graphite",
    red: "border-red-400/30 bg-red-400/10 text-red-300",
  } as const;
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-medium ${map[tone]}`}
    >
      {children}
    </span>
  );
}

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 eyebrow">
      <span className="h-px w-12 bg-indigo-glow/70" />
      <span className="text-indigo-soft">{children}</span>
    </div>
  );
}
