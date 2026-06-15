"use client";
import Link from "next/link";
import { motion } from "framer-motion";
import SmoothScroll from "@/components/SmoothScroll";
import Loader from "@/components/Loader";
import MarketingNav from "@/components/MarketingNav";
import dynamic from "next/dynamic";
import Atmosphere from "@/components/Atmosphere";
import Stage from "@/components/Stage";

// Three.js hero — browser-only, lazy-loaded so it never blocks first paint.
const NodeNetwork = dynamic(() => import("@/components/NodeNetwork"), {
  ssr: false,
});
import TrustStrip from "@/components/TrustStrip";
import { Chip } from "@/components/ui";
import { stagger, fadeUp, EASE } from "@/lib/motion";
import CleanSetPiece from "@/components/setpieces/CleanSetPiece";
import SchemaLabelSetPiece from "@/components/setpieces/SchemaLabelSetPiece";
import JoinDrawSetPiece from "@/components/setpieces/JoinDrawSetPiece";
import QuestionAnswerSetPiece from "@/components/setpieces/QuestionAnswerSetPiece";
import { RAW_ROWS } from "@/lib/mock";

const CHIPS = [
  "Privacy-first",
  "Multi-file JOINs in English",
  "Schema it understands",
  "Answers, not jargon",
];

export default function MarketingPage() {
  return (
    <>
      <Loader />
      <MarketingNav />
      <SmoothScroll>
        <main className="relative">
          {/* ───────── HERO ───────── */}
          <section className="relative flex min-h-[100svh] flex-col justify-end overflow-hidden px-6 pb-16 pt-28 sm:px-10 sm:pb-24">
            {/* code-built animated hero: luminous golden data wave in 3D */}
            <div className="absolute inset-0 z-0">
              <NodeNetwork />
              {/* left→right fade: wave concentrates on the right, fades to black
                  behind the headline on the left so the type stays readable */}
              <div
                className="pointer-events-none absolute inset-0"
                style={{
                  background:
                    "linear-gradient(90deg, rgba(6,6,7,0.88) 0%, rgba(6,6,7,0.5) 22%, rgba(6,6,7,0.15) 46%, transparent 64%)",
                }}
              />
              {/* short floor + top fades only — let the wave rise high */}
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-void to-transparent" />
              <div className="pointer-events-none absolute inset-x-0 top-0 h-32 bg-gradient-to-b from-void/85 to-transparent" />
            </div>

            <div className="relative z-10 mx-auto w-full max-w-[var(--maxw)]">
              <motion.div
                className="mb-8 flex flex-wrap gap-2"
                initial="hidden"
                animate="show"
                variants={stagger}
                transition={{ delayChildren: 1.1 }}
              >
                {CHIPS.map((c) => (
                  <motion.div key={c} variants={fadeUp}>
                    <Chip>{c}</Chip>
                  </motion.div>
                ))}
              </motion.div>

              {/* clip-free reveal — no overflow:hidden, generous line-height + pad
                  so descenders (y, p) and the gold glow are never cut off */}
              <h1
                className="display-mega max-w-[16ch] pb-[0.18em] text-[clamp(3.5rem,12vw,10.5rem)] leading-[1.04] text-ink"
                style={{ textShadow: "0 2px 40px rgba(0,0,0,0.65)" }}
              >
                {["The analyst", "in your pocket."].map((line, i) => (
                  <span key={i} className="block pb-[0.08em]">
                    <motion.span
                      className="inline-block"
                      initial={{ opacity: 0, y: 36 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 1.2 + i * 0.12, duration: 0.9, ease: EASE }}
                    >
                      {i === 1 ? (
                        <>
                          in your <span className="accent-glow">pocket.</span>
                        </>
                      ) : (
                        line
                      )}
                    </motion.span>
                  </span>
                ))}
              </h1>

              <div className="mt-10 flex flex-col gap-8 sm:flex-row sm:items-end sm:justify-between">
                <motion.p
                  className="max-w-md text-[17px] leading-relaxed text-ink-dim"
                  initial={{ opacity: 0, y: 16 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 1.6, duration: 0.8, ease: EASE }}
                >
                  Privacy-first, executive-grade analytics in plain English. Drop in
                  messy files — Javaab cleans them, understands the schema, joins
                  them, and answers like a human.
                </motion.p>

                <motion.div
                  className="flex items-center gap-5"
                  initial={{ opacity: 0, y: 16 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 1.8, duration: 0.8, ease: EASE }}
                >
                  <Link
                    href="/app"
                    className="group relative overflow-hidden rounded-full bg-indigo-glow px-7 py-3.5 text-[15px] font-medium text-white shadow-glow transition hover:brightness-110"
                  >
                    Launch Javaab
                  </Link>
                  <a
                    href="#mess"
                    className="text-[14px] text-graphite transition hover:text-ink"
                  >
                    Skip intro ↓
                  </a>
                </motion.div>
              </div>
            </div>
          </section>

          {/* ───────── THE MESS ───────── */}
          <span id="mess" />
          <Stage
            label="The problem"
            title="Real data arrives as chaos."
            kicker="Mixed date formats. Currency symbols. USA, U.S.A., America. Scattered NA and dashes. Duplicate rows. Every tool chokes — or worse, silently groups it wrong."
          >
            <div className="glass-strong overflow-x-auto rounded-2xl shadow-glass">
              <table className="w-full min-w-[640px] text-left text-[13px]">
                <tbody>
                  {RAW_ROWS.map((row, r) => (
                    <tr
                      key={r}
                      className={`border-t border-[var(--hairline)] first:border-t-0 ${
                        r === 0 ? "text-graphite" : "text-ink/70"
                      }`}
                    >
                      {row.map((cell, c) => (
                        <td key={c} className="tnum whitespace-nowrap px-4 py-2.5">
                          {cell === "" ? (
                            <span className="text-white/20">·</span>
                          ) : (
                            String(cell)
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Stage>

          {/* ───────── THE CLEAN (signature, pinned) ───────── */}
          <Stage
            label="Engine 1 · Cleaning"
            title="Watch it fix itself."
            kicker="Deterministic, reversible, transparent. The LLM never touches a raw cell — this is pure Python you can audit, and every change is logged for one-click undo."
            pin
            pinLength={0.6}
          >
            <CleanSetPiece />
          </Stage>

          {/* ───────── UNDERSTANDING ───────── */}
          <Stage
            label="Engine 3 · Schema"
            title="It understands what the columns mean."
            kicker="Even coded gibberish — cst_id, ord_dt, amt — gets a plain-English meaning and a confidence score. Low confidence? It asks, instead of hallucinating."
          >
            <SchemaLabelSetPiece />
          </Stage>

          {/* ───────── CONNECTION ───────── */}
          <Stage
            label="Engine 2 · Joins"
            title="It connects files by their values."
            kicker="Name-matching isn't enough. Javaab checks that one column's values actually live inside another's — so joins work even when the headers are nonsense."
          >
            <JoinDrawSetPiece />
          </Stage>

          {/* ───────── QUESTION → ANSWER ───────── */}
          <Stage
            label="Ask anything"
            title="Question in. Answer first."
            kicker="Plain English becomes guarded SQL. Then the executive order: the insight leads, the chart follows, the table settles last."
          >
            <div className="glass-strong rounded-2xl p-5 shadow-glass sm:p-7">
              <QuestionAnswerSetPiece />
            </div>
          </Stage>

          {/* ───────── TRUST ───────── */}
          <Stage
            label="Proof, not promises"
            title="Every claim, a real number."
            kicker="These tick from real logs in the app — destructive SQL is blocked 100% of the time, and nothing you upload survives your session."
          >
            <TrustStrip />
          </Stage>

          {/* ───────── PRIVACY ───────── */}
          <section className="relative overflow-hidden px-6 py-32 sm:px-10">
            <Atmosphere />
            <motion.div
              className="relative z-10 mx-auto max-w-3xl text-center"
              variants={fadeUp}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true }}
            >
              <h2 className="display text-[clamp(2rem,5vw,3.4rem)] text-ink">
                Your data leaves no trace.
              </h2>
              <p className="mx-auto mt-6 max-w-2xl text-[15px] leading-relaxed text-graphite">
                Your uploaded data lives in memory only for your session and is wiped
                when you leave. Questions and small data samples are sent to the model
                provider to generate SQL and insights. We default to Groq, whose policy
                is not to retain inference data. Optionally switch to Gemini with your
                own API key. Privacy Mode forces Groq and minimizes what's sent.
              </p>
              <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
                <Link
                  href="/app"
                  className="rounded-full bg-indigo-glow px-6 py-3 text-[15px] font-medium text-white shadow-glow transition hover:brightness-110"
                >
                  Launch Javaab
                </Link>
                <a
                  href="https://github.com/LakshyaMalik18/Javaab-ai"
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-full border border-[var(--hairline)] px-6 py-3 text-[15px] text-ink transition hover:bg-white/5"
                >
                  View source · PolyForm Noncommercial
                </a>
              </div>
            </motion.div>
          </section>

          {/* ───────── FOOTER ───────── */}
          <footer className="border-t border-[var(--hairline)] px-6 py-10 sm:px-10">
            <div className="mx-auto flex max-w-[var(--maxw)] flex-col items-center justify-between gap-4 text-[13px] text-graphite sm:flex-row">
              <span className="display text-[18px] text-ink">
                Javaab<span className="accent">.</span>
              </span>
              <span>The analyst in your pocket. · Built by Lakshya Malik · PolyForm Noncommercial</span>
            </div>
          </footer>
        </main>
      </SmoothScroll>
    </>
  );
}
