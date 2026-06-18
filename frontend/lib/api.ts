// Typed client for the real FastAPI backend (Phase 5B).
// Every response shape is normalized into the contracts in lib/types.ts so the
// components that used to read lib/mock.ts keep working unchanged.
//
// The session id travels in the `X-Session-Id` header on every authenticated
// call. Base URL comes from NEXT_PUBLIC_API_URL, defaulting to localhost:8000.
import type {
  AmbiguityFlag,
  AnswerResult,
  ChangeRecord,
  DuplicateGroup,
  Metrics,
  SchemaContract,
} from "./types";

export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

// ── Errors ───────────────────────────────────────────────────────────────────
export type ApiErrorKind =
  | "rate_limit"
  | "bad_request"
  | "not_found"
  | "conflict"
  | "server"
  | "network"
  | "unknown";

export class ApiError extends Error {
  status: number;
  kind: ApiErrorKind;
  detail: string;
  constructor(kind: ApiErrorKind, status: number, message: string, detail = "") {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.detail = detail;
  }
}

function kindFor(status: number): ApiErrorKind {
  if (status === 429) return "rate_limit";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 400 || status === 415 || status === 422) return "bad_request";
  if (status >= 500) return "server";
  return "unknown";
}

// A friendly, human message for each failure mode (used by toasts).
function friendly(kind: ApiErrorKind, detail: string): string {
  switch (kind) {
    case "rate_limit":
      return "The analysis service is busy right now. Please try again in a moment.";
    case "not_found":
      return "Your session expired. Refresh the page to start a fresh one.";
    case "conflict":
      return detail || "That step isn't ready yet — upload your data first.";
    case "bad_request":
      return detail || "There was a problem with that request.";
    case "server":
      return "The analysis service hit a problem. Please try again.";
    case "network":
      return "Couldn't reach the server. Is the backend running on " + API_URL + "?";
    default:
      return detail || "Something went wrong. Please try again.";
  }
}

function detailString(body: unknown): string {
  if (body && typeof body === "object") {
    const d = (body as Record<string, unknown>).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object") {
      const errs = (d as Record<string, unknown>).errors;
      if (Array.isArray(errs)) return errs.join("; ");
      return JSON.stringify(d);
    }
  }
  return "";
}

// ── Core request helper ──────────────────────────────────────────────────────
interface RequestOpts {
  method?: string;
  sessionId?: string | null;
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
}

async function request<T>(path: string, opts: RequestOpts = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers || {}) };
  if (opts.sessionId) headers["X-Session-Id"] = opts.sessionId;

  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: opts.method || "GET",
      headers,
      body,
    });
  } catch {
    throw new ApiError("network", 0, friendly("network", ""));
  }

  let parsed: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!res.ok) {
    const kind = kindFor(res.status);
    const detail = detailString(parsed);
    throw new ApiError(kind, res.status, friendly(kind, detail), detail);
  }

  return parsed as T;
}

// ── Endpoints ────────────────────────────────────────────────────────────────

export interface SessionInfo {
  session_id: string;
  privacy_mode: boolean;
  provider: string;
  created_at?: number;
  timeout_seconds?: number;
  data_retention: string;
}

export function createSession(input: {
  privacy_mode?: boolean;
  user_key?: string | null;
}): Promise<SessionInfo> {
  return request<SessionInfo>("/session", {
    method: "POST",
    json: { privacy_mode: !!input.privacy_mode, user_key: input.user_key || null },
  });
}

export async function endSession(sessionId: string): Promise<void> {
  // Best-effort wipe — never surface an error to the user on the way out.
  try {
    await request("/session", { method: "DELETE", sessionId });
  } catch {
    /* already gone / unreachable — nothing to clean up client-side */
  }
}

// Fire-and-forget variant for the unload path (fetch with keepalive).
export function endSessionBeacon(sessionId: string): void {
  try {
    fetch(`${API_URL}/session`, {
      method: "DELETE",
      headers: { "X-Session-Id": sessionId },
      keepalive: true,
    }).catch(() => {});
  } catch {
    /* noop */
  }
}

export function health(): Promise<{ status: string; active_sessions: number }> {
  return request("/health");
}

// ── Upload → normalized cleaning report ──────────────────────────────────────
export interface TableMeta {
  name: string;
  row_count: number;
  col_count: number;
  columns: string[];
}

export interface UploadReport {
  tables: TableMeta[];
  ledger: ChangeRecord[];
  totalCellsAffected: number;
  duplicates: DuplicateGroup[];
  ambiguity: AmbiguityFlag[];
  errors: string[];
  // non-fatal notices, e.g. "re-uploading reset your manual joins" — shown to the user
  warnings: string[];
}

interface RawUploadResponse {
  session_id: string;
  tables: TableMeta[];
  ledger: {
    total_cells_affected: number;
    records: Array<Omit<ChangeRecord, "reversible">>;
  };
  flags: Array<Record<string, unknown>>;
  errors: string[];
  warnings?: string[];
}

function normalizeUpload(raw: RawUploadResponse): UploadReport {
  const ledger: ChangeRecord[] = (raw.ledger?.records || []).map((r) => ({
    ...r,
    reversible: true, // every recorded change is reversible by design
  }));

  const duplicates: DuplicateGroup[] = [];
  const ambiguity: AmbiguityFlag[] = [];

  for (const f of raw.flags || []) {
    const kind = String(f.kind || "");
    const table = String(f.table || "");
    if (kind === "ambiguous_date") {
      ambiguity.push({
        column: String(f.column || ""),
        kind: "date_order",
        detail: String(f.detail || ""),
      });
    } else if (kind === "coerce_failed" || kind === "mixed_type") {
      ambiguity.push({
        column: String(f.column || ""),
        kind: kind as AmbiguityFlag["kind"],
        detail: String(f.detail || ""),
      });
    } else if (kind === "exact_duplicate") {
      for (const group of (f.groups as number[][]) || []) {
        duplicates.push({
          row_indices: group,
          sample: { name: table },
          kind: "exact",
        });
      }
    } else if (kind === "near_duplicate") {
      const diffs = (f.diffs as Array<{
        indices: number[];
        fields: string[];
        values: Record<string, [string, string]>;
      }>) || [];
      if (diffs.length) {
        for (const d of diffs) {
          const note = d.fields
            .map((fld) => {
              const [a, b] = d.values[fld] ?? ["", ""];
              return `${fld}: "${a}" vs "${b}"`;
            })
            .join("; ");
          duplicates.push({
            row_indices: d.indices,
            sample: { name: table },
            kind: "near",
            diff: note || undefined,
          });
        }
      } else {
        // backward-compat / mock data: only index pairs, no per-pair detail
        for (const pair of (f.pairs as number[][]) || []) {
          duplicates.push({
            row_indices: pair,
            sample: { name: table },
            kind: "near",
          });
        }
      }
    }
  }

  return {
    tables: raw.tables || [],
    ledger,
    totalCellsAffected: raw.ledger?.total_cells_affected ?? 0,
    duplicates,
    ambiguity,
    errors: raw.errors || [],
    warnings: raw.warnings || [],
  };
}

export async function uploadFiles(
  sessionId: string,
  files: File[],
): Promise<UploadReport> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const raw = await request<RawUploadResponse>("/upload", {
    method: "POST",
    sessionId,
    body: form,
  });
  return normalizeUpload(raw);
}

// ── Custom cleaning rules ────────────────────────────────────────────────────
export type CleaningRuleType = "null_token" | "force_type" | "merge_values";

export interface CleaningRule {
  type: CleaningRuleType;
  column: string;
  table?: string | null;
  params: Record<string, unknown>;
}

// Re-runs cleaning on the retained raw data with the user's rules; returns the same
// report shape as /upload (so the cleaning step re-renders from one normalizer).
export async function applyRules(
  sessionId: string,
  rules: CleaningRule[],
): Promise<UploadReport> {
  const raw = await request<RawUploadResponse>("/apply-rules", {
    method: "POST",
    sessionId,
    json: { rules },
  });
  return normalizeUpload(raw);
}

// ── Resolve duplicates ───────────────────────────────────────────────────────
export interface DuplicateDecision {
  table: string;
  row_indices: number[];
  action: "keep" | "remove";
}

export interface ResolveDuplicatesResult {
  removed_rows: number;
  tables: TableMeta[];
}

export function resolveDuplicates(
  sessionId: string,
  decisions: DuplicateDecision[],
): Promise<ResolveDuplicatesResult> {
  return request<ResolveDuplicatesResult>("/resolve-duplicates", {
    method: "POST",
    sessionId,
    json: { decisions },
  });
}

// ── Schema ───────────────────────────────────────────────────────────────────
export function getSchema(sessionId: string): Promise<SchemaContract> {
  return request<SchemaContract>("/schema", { sessionId });
}

export interface ColumnEdit {
  table: string;
  column: string;
  meaning?: string;
  role?: string;
  dtype?: string;
  confidence?: number;
  provisional?: boolean;
}

export interface DataDictionaryEntry {
  table?: string;
  column: string;
  description: string;
}

export interface RelationshipChoice {
  from_table: string;
  from_col: string;
  to_table: string;
  to_col: string;
}

// A user-defined join. Same shape as a choice; the backend validates it exists,
// persists it, and makes it the active link for its table-pair.
export type ManualRelationship = RelationshipChoice;

export function confirmSchema(
  sessionId: string,
  body: {
    column_edits?: ColumnEdit[];
    data_dictionary?: DataDictionaryEntry[];
    relationship_choices?: RelationshipChoice[];
    manual_relationships?: ManualRelationship[];
  },
): Promise<SchemaContract & { applied?: string[] }> {
  return request("/confirm-schema", {
    method: "POST",
    sessionId,
    json: {
      column_edits: body.column_edits || [],
      data_dictionary: body.data_dictionary || [],
      relationship_choices: body.relationship_choices || [],
      manual_relationships: body.manual_relationships || [],
    },
  });
}

// ── Ask ──────────────────────────────────────────────────────────────────────
export function ask(sessionId: string, question: string): Promise<AnswerResult> {
  return request<AnswerResult>("/ask", {
    method: "POST",
    sessionId,
    json: { question },
  });
}

// ── Metrics ──────────────────────────────────────────────────────────────────
interface RawMetrics {
  session: {
    queries_answered?: number;
    destructive_blocked_pct?: number;
  } | null;
  aggregate?: {
    queries_answered?: number;
    destructive_blocked_pct?: number;
    schema_accuracy_pct?: number;
  };
}

export async function getMetrics(sessionId: string): Promise<Metrics> {
  const raw = await request<RawMetrics>("/metrics", { sessionId });
  const s = raw.session;
  const agg = raw.aggregate || {};
  return {
    queries_answered: s?.queries_answered ?? 0,
    destructive_blocked_pct:
      s?.destructive_blocked_pct ?? agg.destructive_blocked_pct ?? 100,
    schema_accuracy_pct: agg.schema_accuracy_pct ?? 0,
    bytes_retained: 0, // ephemeral by design — nothing is ever persisted
  };
}
