"use client";
// Short loader: 0 → 100 counter, then a reveal wipe into the hero.
import { useEffect, useState } from "react";
import { motion, AnimatePresence, animate } from "framer-motion";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";

export default function Loader() {
  const [pct, setPct] = useState(0);
  const [done, setDone] = useState(false);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    if (reduced) {
      setDone(true);
      return;
    }
    const controls = animate(0, 100, {
      duration: 1.6,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setPct(Math.round(v)),
      onComplete: () => setTimeout(() => setDone(true), 250),
    });
    return () => controls.stop();
  }, [reduced]);

  return (
    <AnimatePresence>
      {!done && (
        <motion.div
          className="fixed inset-0 z-[100] flex items-end justify-between bg-void px-8 pb-10 sm:px-12"
          exit={{ y: "-100%" }}
          transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
        >
          <span className="display-mega text-[26vw] leading-none text-ink tnum sm:text-[18vw]">
            {pct}
          </span>
          <span className="mb-5 eyebrow">Javaab</span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
