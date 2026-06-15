"use client";
import { useEffect, useRef, useState } from "react";
import { usePrefersReducedMotion } from "./useReducedMotion";

/** Types out `text` when `start` becomes true. Reduced motion → instant. */
export function useTypewriter(text: string, start: boolean, cps = 45) {
  const [out, setOut] = useState("");
  const reduced = usePrefersReducedMotion();
  const done = out.length === text.length;
  const ref = useRef<number | null>(null);

  useEffect(() => {
    if (!start) {
      setOut("");
      return;
    }
    if (reduced) {
      setOut(text);
      return;
    }
    let i = 0;
    const step = () => {
      i += 1;
      setOut(text.slice(0, i));
      if (i < text.length) {
        ref.current = window.setTimeout(step, 1000 / cps);
      }
    };
    ref.current = window.setTimeout(step, 1000 / cps);
    return () => {
      if (ref.current) clearTimeout(ref.current);
    };
  }, [text, start, reduced, cps]);

  return { out, done };
}
