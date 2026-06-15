"use client";
import Link from "next/link";
import { motion } from "framer-motion";

export default function MarketingNav() {
  return (
    <motion.nav
      initial={{ opacity: 0, y: -16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 1, duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
      className="fixed inset-x-0 top-0 z-50 flex items-center justify-between px-6 py-4 sm:px-10"
    >
      <Link href="/" className="display text-[20px] tracking-tight text-ink">
        Javaab<span className="accent">.</span>
      </Link>
      <div className="flex items-center gap-3">
        <a
          href="https://github.com/LakshyaMalik18/Javaab-ai"
          target="_blank"
          rel="noreferrer"
          className="hidden text-[13px] text-graphite transition hover:text-ink sm:block"
        >
          GitHub
        </a>
        <Link
          href="/app"
          className="rounded-full bg-indigo-glow px-4 py-2 text-[13px] font-medium text-white shadow-glow transition hover:brightness-110"
        >
          Launch Javaab
        </Link>
      </div>
    </motion.nav>
  );
}
