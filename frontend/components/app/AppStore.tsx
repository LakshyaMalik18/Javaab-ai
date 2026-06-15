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
  provider: "groq" | "gemini";
  userKey: string;
}

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
  ) => Promise<boolean>;

  // ask
  ask: (question: string) => Promise<AnswerResult>;

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
  privacyMode: true, // privacy-first by default — forces Groq, minimal sample
  provider: "groq",
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

  // keep the latest session id for the unload handler without re-binding it
  const sessionRef = useRef<string | null>(null);
  sessionRef.current = sessionId;

  const clearDerived = useCallback(() => {
    setReport(null);
    setSchema(null);
    setSchemaError(null);
    setMetrics(null);
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
    ): Promise<boolean> => {
      console.log("[confirmSchema] called", {
        sessionId,
        edits: edits.length,
        dict: dict.length,
      });
      if (!sessionId) {
        console.warn("[confirmSchema] no sessionId — bailing");
        return false;
      }
      try {
        const s = await api.confirmSchema(sessionId, {
          column_edits: edits,
          data_dictionary: dict,
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
        return await api.ask(sessionId, question);
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : "That query failed.";
        toast(msg, "error");
        return errorAnswer(question, msg);
      }
    },
    [sessionId, toast],
  );

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
        ask,
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
