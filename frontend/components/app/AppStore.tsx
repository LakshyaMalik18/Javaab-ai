"use client";
// The single client-side data layer for /app. Holds the live session, the real
// upload report, schema contract and metrics, and exposes the async actions the
// step components call. Replaces every lib/mock.ts import in the app flow.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { AnswerResult, Metrics, SchemaContract } from "@/lib/types";
import { useToast } from "./Toaster";

export interface Settings {
  privacyMode: boolean;
  userKey: string;
}

export type ProviderName = "groq" | "gemini";

interface AppData {
  // session
  sessionId: string | null;
  sessionStatus: "creating" | "ready" | "error";
  resetToken: number; // bumps when the session is recreated → flow restarts
  retrySession: () => void;

  // settings
  settings: Settings;
  applySettings: (next: Settings) => Promise<void>;
  applyingSettings: boolean;

  // upload
  report: api.UploadReport | null;
  uploading: boolean;
  uploadFiles: (files: File[]) => Promise<boolean>;

  // schema
  schema: SchemaContract | null;
  schemaLoading: boolean;
  schemaError: string | null;
  loadSchema: (force?: boolean) => Promise<void>;
  confirmSchema: (
    edits: api.ColumnEdit[],
    dict: api.DataDictionaryEntry[],
    relationshipChoices?: api.RelationshipChoice[],
    manualRelationships?: api.ManualRelationship[],
  ) => Promise<boolean>;

  // cleaning — explicit duplicate removal (returns rows actually removed, or null on error)
  resolveDuplicates: (
    decisions: api.DuplicateDecision[],
  ) => Promise<number | null>;

  // cleaning — add custom rules; re-runs cleaning from raw and refreshes the report
  applyRules: (rules: api.CleaningRule[]) => Promise<boolean>;

  // ask
  ask: (question: string) => Promise<AnswerResult>;

  // provider indicator (read-only, reflects the provider that ACTUALLY answered)
  primaryProvider: ProviderName; // the configured primary for the live session
  activeProvider: ProviderName; // who handled the most recent answer (or primary)
  fallbackNote: string | null; // honest note when the last answer fell back

  // metrics
  metrics: Metrics | null;
  metricsLoading: boolean;
  loadMetrics: () => Promise<void>;
}

const Ctx = createContext<AppData | null>(null);

export function useAppData(): AppData {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAppData must be used within <AppStoreProvider>");
  return ctx;
}

const DEFAULT_SETTINGS: Settings = {
  // Privacy Mode OFF for now → default mode (Gemini primary, Groq fallback).
  // The default flips after a quality comparison.
  privacyMode: false,
  userKey: "",
};

export function AppStoreProvider({ children }: { children: React.ReactNode }) {
  const { toast } = useToast();

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionStatus, setSessionStatus] = useState<AppData["sessionStatus"]>(
    "creating",
  );
  const [resetToken, setResetToken] = useState(0);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [applyingSettings, setApplyingSettings] = useState(false);

  const [report, setReport] = useState<api.UploadReport | null>(null);
  const [uploading, setUploading] = useState(false);

  const [schema, setSchema] = useState<SchemaContract | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(false);

  // who actually handled the last answer. The backend only sets provider_used
  // when the default-mode primary fell back, so null here means "the primary
  // answered" (or nothing has been asked yet) → the indicator shows the primary.
  const [lastAnswerProvider, setLastAnswerProvider] = useState<string | null>(
    null,
  );
  const [fallbackNote, setFallbackNote] = useState<string | null>(null);

  // keep the latest session id for the unload handler without re-binding it
  const sessionRef = useRef<string | null>(null);
  sessionRef.current = sessionId;

  const clearDerived = useCallback(() => {
    setReport(null);
    setSchema(null);
    setSchemaError(null);
    setMetrics(null);
    // a fresh session has answered nothing yet → indicator resets to the primary
    setLastAnswerProvider(null);
    setFallbackNote(null);
  }, []);

  const spinUp = useCallback(
    async (s: Settings, isReset: boolean) => {
      setSessionStatus("creating");
      try {
        const info = await api.createSession({
          privacy_mode: s.privacyMode,
          user_key: s.privacyMode ? null : s.userKey || null,
        });
        setSessionId(info.session_id);
        setSessionStatus("ready");
        if (isReset) setResetToken((t) => t + 1);
      } catch (e) {
        setSessionStatus("error");
        toast(
          e instanceof ApiError
            ? e.message
            : "Couldn't start a session. Is the backend running?",
        );
      }
    },
    [toast],
  );

  // create the session on entering /app
  useEffect(() => {
    let alive = true;
    (async () => {
      if (!alive) return;
      await spinUp(DEFAULT_SETTINGS, false);
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // wipe the session on leave / unload so the ephemeral guarantee actually fires
  useEffect(() => {
    const onUnload = () => {
      if (sessionRef.current) api.endSessionBeacon(sessionRef.current);
    };
    window.addEventListener("pagehide", onUnload);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      window.removeEventListener("pagehide", onUnload);
      window.removeEventListener("beforeunload", onUnload);
      if (sessionRef.current) api.endSession(sessionRef.current);
    };
  }, []);

  const retrySession = useCallback(() => {
    spinUp(settings, false);
  }, [spinUp, settings]);

  const applySettings = useCallback(
    async (next: Settings) => {
      setSettings(next);
      // No update endpoint exists — settings are bound at session creation.
      // Recreate the session so the new privacy_mode / key truly take effect.
      setApplyingSettings(true);
      const old = sessionRef.current;
      clearDerived();
      await spinUp(next, true);
      if (old) api.endSession(old);
      setApplyingSettings(false);
    },
    [spinUp, clearDerived],
  );

  const uploadFiles = useCallback(
    async (files: File[]): Promise<boolean> => {
      if (!sessionId) {
        toast("Session isn't ready yet — give it a second.");
        return false;
      }
      setUploading(true);
      try {
        const r = await api.uploadFiles(sessionId, files);
        setReport(r);
        setSchema(null); // a fresh upload invalidates any prior schema
        if (r.errors.length && !r.tables.length) {
          toast("Couldn't read those files: " + r.errors.join("; "));
          return false;
        }
        if (r.errors.length) {
          toast("Some files had issues: " + r.errors.join("; "), "info");
        }
        // re-upload may have reset manually-defined joins — tell the user, don't drop silently
        for (const w of r.warnings) toast(w, "info");
        return true;
      } catch (e) {
        toast(e instanceof ApiError ? e.message : "Upload failed.");
        return false;
      } finally {
        setUploading(false);
      }
    },
    [sessionId, toast],
  );

  const resolveDuplicates = useCallback(
    async (decisions: api.DuplicateDecision[]): Promise<number | null> => {
      if (!sessionId) {
        toast("Session isn't ready yet — give it a second.");
        return null;
      }
      // nothing to remove → no backend call, report zero removed
      if (!decisions.some((d) => d.action === "remove")) return 0;
      try {
        const res = await api.resolveDuplicates(sessionId, decisions);
        return res.removed_rows;
      } catch (e) {
        toast(e instanceof ApiError ? e.message : "Couldn't remove the duplicates.");
        return null;
      }
    },
    [sessionId, toast],
  );

  const applyRules = useCallback(
    async (rules: api.CleaningRule[]): Promise<boolean> => {
      if (!sessionId) {
        toast("Session isn't ready yet — give it a second.");
        return false;
      }
      try {
        const r = await api.applyRules(sessionId, rules);
        setReport(r); // re-render the cleaning report on the freshly-cleaned data
        setSchema(null); // a re-clean changes the data → any prior schema is stale
        // applying a rule rebuilds the contract — warn if it reset manual joins
        for (const w of r.warnings) toast(w, "info");
        return true;
      } catch (e) {
        toast(e instanceof ApiError ? e.message : "Couldn't apply that rule.");
        return false;
      }
    },
    [sessionId, toast],
  );

  const loadSchema = useCallback(
    async (force = false) => {
      if (!sessionId) return;
      if (schema && !force) return;
      setSchemaLoading(true);
      setSchemaError(null);
      try {
        const s = await api.getSchema(sessionId);
        setSchema(s);
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : "Couldn't load the schema.";
        setSchemaError(msg);
        toast(msg);
      } finally {
        setSchemaLoading(false);
      }
    },
    [sessionId, schema, toast],
  );

  const confirmSchema = useCallback(
    async (
      edits: api.ColumnEdit[],
      dict: api.DataDictionaryEntry[],
      relationshipChoices: api.RelationshipChoice[] = [],
      manualRelationships: api.ManualRelationship[] = [],
    ): Promise<boolean> => {
      console.log("[confirmSchema] called", {
        sessionId,
        edits: edits.length,
        dict: dict.length,
        relationshipChoices: relationshipChoices.length,
        manualRelationships: manualRelationships.length,
      });
      if (!sessionId) {
        console.warn("[confirmSchema] no sessionId — bailing");
        return false;
      }
      try {
        const s = await api.confirmSchema(sessionId, {
          column_edits: edits,
          data_dictionary: dict,
          relationship_choices: relationshipChoices,
          manual_relationships: manualRelationships,
        });
        console.log("[confirmSchema] API ok", s);
        setSchema(s);
        return true;
      } catch (e) {
        console.error("[confirmSchema] API failed", e);
        toast(e instanceof ApiError ? e.message : "Couldn't save schema edits.");
        return false;
      }
    },
    [sessionId, toast],
  );

  const ask = useCallback(
    async (question: string): Promise<AnswerResult> => {
      if (!sessionId) {
        const msg = "Session isn't ready yet — give it a second.";
        toast(msg);
        return errorAnswer(question, msg);
      }
      try {
        const res = await api.ask(sessionId, question);
        // Record who REALLY answered, straight from the backend signal — not the
        // setting. provider_used is present only when the primary fell back to
        // Groq; otherwise the primary handled it (null → indicator shows primary).
        if (res.status === "answered") {
          setLastAnswerProvider(res.provider_used ?? null);
          setFallbackNote(res.fallback_note ?? null);
        }
        return res;
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : "That query failed.";
        toast(msg, "error");
        return errorAnswer(question, msg);
      }
    },
    [sessionId, toast],
  );

  // The configured primary for the live session: Groq in Privacy Mode, else
  // Gemini. Used as the indicator's pre-answer / no-fallback state.
  const primaryProvider: ProviderName = settings.privacyMode ? "groq" : "gemini";
  // The provider that ACTUALLY handled the last answer, falling back to the
  // configured primary when nothing has answered yet or the primary served it.
  const activeProvider: ProviderName =
    lastAnswerProvider === "groq" || lastAnswerProvider === "gemini"
      ? lastAnswerProvider
      : primaryProvider;

  const loadMetrics = useCallback(async () => {
    if (!sessionId) return;
    setMetricsLoading(true);
    try {
      const m = await api.getMetrics(sessionId);
      setMetrics(m);
    } catch {
      /* trust panel just shows a loading/blank state — never block */
    } finally {
      setMetricsLoading(false);
    }
  }, [sessionId]);

  return (
    <Ctx.Provider
      value={{
        sessionId,
        sessionStatus,
        resetToken,
        retrySession,
        settings,
        applySettings,
        applyingSettings,
        report,
        uploading,
        uploadFiles,
        schema,
        schemaLoading,
        schemaError,
        loadSchema,
        confirmSchema,
        resolveDuplicates,
        applyRules,
        ask,
        primaryProvider,
        activeProvider,
        fallbackNote,
        metrics,
        metricsLoading,
        loadMetrics,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

function errorAnswer(question: string, msg: string): AnswerResult {
  return {
    status: "error",
    question,
    insight: null,
    sql: null,
    assumptions: [],
    followups: [],
    clarifying_question: null,
    blocked_reason: null,
    chart_hint: null,
    columns: [],
    rows: [],
    tables_used: [],
    error: msg,
  };
}
