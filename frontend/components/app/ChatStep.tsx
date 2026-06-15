"use client";
import { useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { AnswerResult } from "@/lib/types";
import AnswerCard from "./AnswerCard";
import { useAppData } from "./AppStore";

interface Exchange {
  id: number;
  question: string;
  answer: AnswerResult | null; // null = loading
}

const SUGGESTIONS = [
  "Which countries drove the most revenue last quarter?",
  "Top 5 customers by spend",
  "How did refunds trend over time?",
];

export default function ChatStep() {
  const { ask: askApi } = useAppData();
  const [input, setInput] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const idRef = useRef(0);
  const endRef = useRef<HTMLDivElement>(null);

  const ask = async (q: string) => {
    const question = q.trim();
    if (!question) return;
    setInput("");
    const id = ++idRef.current;
    setExchanges((e) => [...e, { id, question, answer: null }]);

    const answer = await askApi(question);
    setExchanges((e) => e.map((x) => (x.id === id ? { ...x, answer } : x)));
    setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 80);
  };

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col">
      <header className="mb-4">
        <h1 className="display text-[clamp(1.6rem,3.5vw,2.2rem)] text-ink">
          Ask in plain English.
        </h1>
        <p className="mt-1 text-[14px] text-graphite">
          Insight first, then the chart, then the data. Try “delete all orders” to
          see the guardrail.
        </p>
      </header>

      <div className="flex-1 space-y-8 overflow-y-auto pb-4">
        {exchanges.length === 0 && (
          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => ask(s)}
                className="rounded-full border border-[var(--hairline)] bg-white/[0.02] px-3 py-1.5 text-[12.5px] text-graphite transition hover:text-ink"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {exchanges.map((x) => (
          <div key={x.id} className="space-y-3">
            <div className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-indigo-glow/15 px-4 py-2.5 text-[14px] text-ink">
                {x.question}
              </div>
            </div>
            <AnimatePresence mode="wait">
              {x.answer === null ? (
                <Thinking key="t" />
              ) : (
                <AnswerCard key="a" answer={x.answer} onFollowup={ask} />
              )}
            </AnimatePresence>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {/* composer */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
        className="sticky bottom-0 mt-2 flex items-center gap-2 rounded-2xl border border-[var(--hairline)] bg-obsidian-800/90 p-2 backdrop-blur"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask anything about your data…"
          className="flex-1 bg-transparent px-3 py-2 text-[14px] text-ink placeholder:text-graphite/60 focus:outline-none"
        />
        <button
          type="submit"
          className="rounded-xl bg-indigo-glow px-4 py-2 text-[14px] font-medium text-white shadow-glow transition hover:brightness-110"
        >
          Ask
        </button>
      </form>
    </div>
  );
}

function Thinking() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="flex items-center gap-2 text-[13px] text-graphite"
    >
      <span className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-indigo-glow"
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1, repeat: Infinity, delay: i * 0.18 }}
          />
        ))}
      </span>
      reading schema · writing SQL · checking the guardrail…
    </motion.div>
  );
}
