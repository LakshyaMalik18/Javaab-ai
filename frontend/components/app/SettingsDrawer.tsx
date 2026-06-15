"use client";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useAppData } from "./AppStore";

export default function SettingsDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { settings, applySettings, applyingSettings } = useAppData();
  const [privacy, setPrivacy] = useState(settings.privacyMode);
  const [provider, setProvider] = useState<"groq" | "gemini">(settings.provider);
  const [key, setKey] = useState(settings.userKey);

  // re-sync the form whenever the drawer reopens with the live settings
  useEffect(() => {
    if (open) {
      setPrivacy(settings.privacyMode);
      setProvider(settings.provider);
      setKey(settings.userKey);
    }
  }, [open, settings]);

  const dirty =
    privacy !== settings.privacyMode ||
    provider !== settings.provider ||
    key !== settings.userKey;

  const apply = async () => {
    await applySettings({
      privacyMode: privacy,
      provider: privacy ? "groq" : provider,
      userKey: privacy ? "" : key,
    });
    onClose();
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/50"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            className="fixed right-0 top-0 z-50 h-full w-full max-w-md overflow-y-auto border-l border-[var(--hairline)] bg-obsidian-800 p-6"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "tween", duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="flex items-center justify-between">
              <h2 className="display text-[22px] text-ink">Settings</h2>
              <button
                onClick={onClose}
                className="text-graphite transition hover:text-ink"
              >
                ✕
              </button>
            </div>

            {/* Privacy Mode */}
            <div className="mt-8 rounded-xl border border-[var(--hairline)] p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-[14px] text-ink">Privacy Mode</div>
                  <div className="mt-0.5 text-[12px] text-graphite">
                    Forces Groq (no-retention) and minimizes the data sample sent.
                  </div>
                </div>
                <Toggle on={privacy} onClick={() => setPrivacy((v) => !v)} />
              </div>
            </div>

            {/* Provider */}
            <div className="mt-4 rounded-xl border border-[var(--hairline)] p-4">
              <div className="text-[14px] text-ink">Model provider</div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {(["groq", "gemini"] as const).map((p) => (
                  <button
                    key={p}
                    disabled={privacy && p === "gemini"}
                    onClick={() => setProvider(p)}
                    className={`rounded-lg border px-3 py-2 text-[13px] transition ${
                      provider === p
                        ? "border-indigo-glow/50 bg-indigo-glow/10 text-indigo-soft"
                        : "border-[var(--hairline)] text-graphite hover:text-ink"
                    } ${privacy && p === "gemini" ? "cursor-not-allowed opacity-40" : ""}`}
                  >
                    {p === "groq" ? "Groq · llama-3.3-70b" : "Gemini 2.5 Flash"}
                  </button>
                ))}
              </div>

              {/* BYO key */}
              <div className="mt-4">
                <label className="text-[12px] text-graphite">
                  Bring your own Gemini key
                </label>
                <input
                  type="password"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  disabled={privacy}
                  placeholder={privacy ? "disabled in Privacy Mode" : "AIza…"}
                  className="mt-1.5 w-full rounded-lg border border-[var(--hairline)] bg-obsidian-700 px-3 py-2 text-[13px] text-ink placeholder:text-graphite/60 disabled:opacity-40"
                />
                <p className="mt-2 text-[11px] leading-relaxed text-graphite/70">
                  Your key is used only for this session and never stored on our
                  servers. Google&apos;s free tier may retain prompts.
                </p>
              </div>
            </div>

            {/* apply — settings bind at session creation, so this starts fresh */}
            <button
              onClick={apply}
              disabled={!dirty || applyingSettings}
              className="mt-6 w-full rounded-full bg-indigo-glow py-3 text-[14px] font-medium text-white shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {applyingSettings ? "Applying…" : "Apply settings"}
            </button>
            {dirty && (
              <p className="mt-2 text-center text-[11px] text-graphite/70">
                Applying starts a fresh, empty session — your current upload will
                be wiped.
              </p>
            )}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`relative h-6 w-11 rounded-full transition ${
        on ? "bg-indigo-glow shadow-glow" : "bg-white/15"
      }`}
    >
      <motion.span
        layout
        className="absolute top-0.5 h-5 w-5 rounded-full bg-white"
        animate={{ left: on ? 22 : 2 }}
      />
    </button>
  );
}
