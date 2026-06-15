"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import Atmosphere from "@/components/Atmosphere";
import StepRail, { STEPS, StepId } from "@/components/app/StepRail";
import UploadStep from "@/components/app/UploadStep";
import CleaningStep from "@/components/app/CleaningStep";
import SchemaStep from "@/components/app/SchemaStep";
import GraphStep from "@/components/app/GraphStep";
import ChatStep from "@/components/app/ChatStep";
import SettingsDrawer from "@/components/app/SettingsDrawer";
import TrustPanel from "@/components/app/TrustPanel";
import { ToastProvider } from "@/components/app/Toaster";
import { AppStoreProvider, useAppData } from "@/components/app/AppStore";

export default function AppPage() {
  return (
    <ToastProvider>
      <AppStoreProvider>
        <AppShell />
      </AppStoreProvider>
    </ToastProvider>
  );
}

function AppShell() {
  const [step, setStep] = useState<StepId>("upload");
  const [reached, setReached] = useState<Set<StepId>>(new Set(["upload"]));
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [trustOpen, setTrustOpen] = useState(false);
  const { resetToken } = useAppData();

  // a recreated session (e.g. settings change) wipes data → restart the flow
  useEffect(() => {
    if (resetToken === 0) return;
    setStep("upload");
    setReached(new Set(["upload"]));
  }, [resetToken]);

  const go = (s: StepId) => {
    setReached((r) => new Set(r).add(s));
    setStep(s);
  };
  const next = () => {
    const i = STEPS.findIndex((s) => s.id === step);
    if (i < STEPS.length - 1) go(STEPS[i + 1].id);
  };

  return (
    <div className="relative flex min-h-screen flex-col">
      <div className="pointer-events-none fixed inset-0 opacity-60">
        <Atmosphere />
      </div>

      {/* top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-[var(--hairline)] bg-obsidian/70 px-5 py-3 backdrop-blur">
        <Link href="/" className="display text-[18px] text-ink">
          Javaab<span className="accent">.</span>
        </Link>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setTrustOpen(true)}
            className="flex items-center gap-2 rounded-full border border-[var(--hairline)] px-3 py-1.5 text-[12.5px] text-graphite transition hover:text-ink"
          >
            <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-glow shadow-glow" />
            Trust
          </button>
          <button
            onClick={() => setSettingsOpen(true)}
            className="rounded-full border border-[var(--hairline)] px-3 py-1.5 text-[12.5px] text-graphite transition hover:text-ink"
          >
            Settings
          </button>
        </div>
      </header>

      <div className="relative z-10 mx-auto flex w-full max-w-[var(--maxw)] flex-1 gap-12 px-5 py-12 lg:gap-16">
        {/* left rail — deliberately light secondary chrome */}
        <aside className="hidden w-36 shrink-0 md:block">
          <div className="sticky top-28">
            <div className="eyebrow mb-4 pl-4 text-graphite/60">Flow</div>
            <StepRail active={step} reached={reached} onGo={go} />
            <div className="mt-10 pl-4 text-[10.5px] leading-relaxed text-graphite/50">
              Ephemeral session — your data lives in memory only and is wiped when
              you leave.
            </div>
          </div>
        </aside>

        {/* mobile step pills */}
        <div className="fixed inset-x-0 bottom-0 z-20 flex justify-center gap-1 border-t border-[var(--hairline)] bg-obsidian/90 px-3 py-2 backdrop-blur md:hidden">
          {STEPS.map((s) => (
            <button
              key={s.id}
              disabled={!reached.has(s.id)}
              onClick={() => reached.has(s.id) && go(s.id)}
              className={`rounded-full px-3 py-1 text-[11px] ${
                step === s.id
                  ? "bg-indigo-glow/20 text-indigo-soft"
                  : reached.has(s.id)
                    ? "text-graphite"
                    : "text-graphite/30"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>

        {/* content */}
        <main className="min-h-[70vh] flex-1 pb-20 md:pb-0">
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
              className="h-full"
            >
              {step === "upload" && <UploadStep onNext={next} />}
              {step === "cleaning" && <CleaningStep onNext={next} />}
              {step === "schema" && <SchemaStep onNext={next} />}
              {step === "graph" && <GraphStep onNext={next} />}
              {step === "chat" && <ChatStep />}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>

      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <TrustPanel open={trustOpen} onClose={() => setTrustOpen(false)} />
    </div>
  );
}
