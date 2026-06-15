"use client";
import { useEffect } from "react";
import Lenis from "lenis";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";

/** Lenis smooth scroll, synced to GSAP's ticker. No-ops under reduced motion. */
export default function SmoothScroll({
  children,
}: {
  children: React.ReactNode;
}) {
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    if (reduced) return;
    const lenis = new Lenis({
      duration: 1.1,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      smoothWheel: true,
    });
    let raf = 0;
    const loop = (time: number) => {
      lenis.raf(time);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    // Bridge to GSAP ScrollTrigger if present
    (window as any).__lenis = lenis;
    return () => {
      cancelAnimationFrame(raf);
      lenis.destroy();
      (window as any).__lenis = null;
    };
  }, [reduced]);

  return <>{children}</>;
}
