"use client";
// A full-viewport cinematic stage — "one idea per screen". Optional GSAP pin
// holds the stage while its set-piece resolves (the scroll-scrubbed feel),
// disabled under reduced motion / mobile.
import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { fadeUp } from "@/lib/motion";
import { SectionLabel } from "@/components/ui";
import { usePrefersReducedMotion, useIsMobile } from "@/lib/useReducedMotion";

export default function Stage({
  label,
  title,
  kicker,
  children,
  pin = false,
  pinLength = 0.8,
}: {
  label: string;
  title: React.ReactNode;
  kicker?: React.ReactNode;
  children: React.ReactNode;
  pin?: boolean;
  pinLength?: number;
}) {
  const ref = useRef<HTMLElement>(null);
  const reduced = usePrefersReducedMotion();
  const mobile = useIsMobile();

  useEffect(() => {
    if (!pin || reduced || mobile || !ref.current) return;
    let trigger: any;
    let mounted = true;
    (async () => {
      const gsapMod = await import("gsap");
      const stMod = await import("gsap/ScrollTrigger");
      if (!mounted || !ref.current) return;
      const gsap = gsapMod.gsap ?? gsapMod.default;
      const ScrollTrigger = stMod.ScrollTrigger ?? stMod.default;
      gsap.registerPlugin(ScrollTrigger);
      trigger = ScrollTrigger.create({
        trigger: ref.current,
        start: "top top",
        end: `+=${Math.round(pinLength * window.innerHeight)}`,
        pin: true,
        pinSpacing: true,
        scrub: false,
      });
    })();
    return () => {
      mounted = false;
      if (trigger) trigger.kill();
    };
  }, [pin, reduced, mobile, pinLength]);

  return (
    <section
      ref={ref}
      className="relative flex min-h-screen w-full flex-col justify-center px-6 py-32 sm:px-10"
    >
      <div className="mx-auto w-full max-w-[var(--maxw)]">
        <motion.div
          variants={fadeUp}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: "-100px" }}
        >
          <SectionLabel>{label}</SectionLabel>
          <h2 className="display mt-5 max-w-4xl text-[clamp(2.5rem,7vw,5.5rem)] text-ink">
            {title}
          </h2>
          {kicker && (
            <p className="mt-7 max-w-2xl text-[17px] leading-relaxed text-graphite">
              {kicker}
            </p>
          )}
        </motion.div>
        <div className="mt-14">{children}</div>
      </div>
    </section>
  );
}
