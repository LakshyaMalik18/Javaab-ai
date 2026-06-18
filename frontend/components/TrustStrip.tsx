"use client";
// Verifiable, real properties of Javaab — no usage counts or accuracy figures,
// because we have no analytics or eval backing those. Presented as an asymmetric
// bento: "0 bytes" is the dominant hero tile (the strongest, most on-brand claim),
// with the other four arranged around it at varied proportions. Gold is reserved
// for the hero and the genuine numbers; the prose facts stay in ink. Numbers count
// up once the grid scrolls into view. Reduced-motion users get the static
// composition with the final figures and no movement.
import { useEffect, useRef, useState } from "react";
import { animate, motion, useInView } from "framer-motion";
import { EASE } from "@/lib/motion";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";

// Counts 0 → `to` once the grid is in view. Reduced motion → shows `to` at once.
function CountUp({
  to,
  decimals = 0,
  start,
  reduced,
}: {
  to: number;
  decimals?: number;
  start: boolean;
  reduced: boolean;
}) {
  const [v, setV] = useState(reduced ? to : 0);
  useEffect(() => {
    if (reduced) {
      setV(to);
      return;
    }
    if (!start) return;
    const c = animate(0, to, {
      duration: 1.5,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (x) => setV(x),
    });
    return () => c.stop();
  }, [start, to, reduced]);
  return <span className="tnum">{v.toFixed(decimals)}</span>;
}

export default function TrustStrip() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });
  const reduced = usePrefersReducedMotion();

  // gentle staggered rise; a no-op set under reduced motion
  const container = reduced
    ? undefined
    : { hidden: {}, show: { transition: { staggerChildren: 0.07, delayChildren: 0.04 } } };
  const item = reduced
    ? undefined
    : {
        hidden: { opacity: 0, y: 24 },
        show: { opacity: 1, y: 0, transition: { duration: 0.7, ease: EASE } },
      };

  // shared tile shell — consistent rounding, hairline border, subtle surface
  const tile =
    "relative overflow-hidden rounded-3xl border border-[var(--hairline)] bg-white/[0.02] p-6 sm:p-7";

  return (
    <motion.div
      ref={ref}
      variants={container}
      initial={reduced ? undefined : "hidden"}
      whileInView={reduced ? undefined : "show"}
      viewport={{ once: true, margin: "-80px" }}
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-4 lg:grid-rows-[minmax(11rem,1fr)_minmax(11rem,1fr)_minmax(7rem,auto)]"
    >
      {/* ── HERO · 0 bytes (dominant, gold) ── */}
      <motion.div
        variants={item}
        className={`${tile} flex flex-col justify-center border-indigo-glow/25 bg-gradient-to-br from-indigo-glow/[0.10] via-indigo-glow/[0.03] to-transparent p-8 sm:p-10 sm:col-span-2 lg:row-span-2 lg:col-start-1 lg:row-start-1`}
      >
        <div
          aria-hidden
          className="pointer-events-none absolute -right-16 -top-16 h-64 w-64 rounded-full opacity-70 blur-3xl"
          style={{ background: "radial-gradient(circle, rgba(232,179,57,0.18), transparent 70%)" }}
        />
        <div className="display leading-[0.92] tracking-tight text-[clamp(3.5rem,9vw,7rem)]">
          <span className="accent">
            <CountUp to={0} start={inView} reduced={reduced} />
          </span>
          <span className="text-ink/50"> bytes</span>
        </div>
        <p className="mt-4 max-w-xs text-[16px] leading-relaxed text-graphite sm:text-[17px]">
          written to disk — wiped on exit
        </p>
      </motion.div>

      {/* ── 4 guards (wide, gold number) ── */}
      <motion.div
        variants={item}
        className={`${tile} flex flex-col justify-center sm:col-span-2 lg:col-start-3 lg:row-start-1`}
      >
        <div className="display leading-none tracking-tight text-[clamp(2.4rem,5vw,3.6rem)]">
          <span className="accent">
            <CountUp to={4} start={inView} reduced={reduced} />
          </span>
          <span className="text-ink/55"> guards</span>
        </div>
        <p className="mt-3 text-[14px] leading-relaxed text-graphite">
          checked on every query
        </p>
      </motion.div>

      {/* ── Real DB (small, ink) ── */}
      <motion.div
        variants={item}
        className={`${tile} flex flex-col justify-center lg:col-start-3 lg:row-start-2`}
      >
        <div className="display leading-none tracking-tight text-ink text-[clamp(1.7rem,3vw,2.3rem)]">
          Real DB
        </div>
        <p className="mt-3 text-[13.5px] leading-relaxed text-graphite">
          answers computed by a database, not the model
        </p>
      </motion.div>

      {/* ── 3 formats (small, gold number) ── */}
      <motion.div
        variants={item}
        className={`${tile} flex flex-col justify-center lg:col-start-4 lg:row-start-2`}
      >
        <div className="display leading-none tracking-tight text-[clamp(1.9rem,3.2vw,2.6rem)]">
          <span className="accent">
            <CountUp to={3} start={inView} reduced={reduced} />
          </span>
          <span className="text-ink/55"> formats</span>
        </div>
        <p className="mt-3 text-[13.5px] leading-relaxed text-graphite">
          CSV, Excel &amp; JSON
        </p>
      </motion.div>

      {/* ── Fails loud (full-width closing strip, ink) ── */}
      <motion.div
        variants={item}
        className={`${tile} flex flex-col justify-center gap-1.5 sm:col-span-2 sm:flex-row sm:items-baseline sm:gap-5 lg:col-span-4 lg:col-start-1 lg:row-start-3`}
      >
        <div className="display leading-none tracking-tight text-ink text-[clamp(1.8rem,3vw,2.4rem)]">
          Fails loud
        </div>
        <p className="text-[14px] leading-relaxed text-graphite">
          never guesses — it refuses rather than fabricate
        </p>
      </motion.div>
    </motion.div>
  );
}
