// TypeScript mirrors of backend/app/models.py contracts.
// Phase 5B swaps the mock data in lib/mock.ts for real API responses of these
// exact shapes — components never change.

export interface ChangeRecord {
  table: string;
  column: string;
  rule: string;
  cells_affected: number;
  before_sample: (string | number | null)[];
  after_sample: (string | number | null)[];
  reversible: boolean;
}

export interface DuplicateGroup {
  row_indices: number[];
  sample: Record<string, string | number | null>;
  kind: "exact" | "near";
  // for near-dups: a one-line "why" — which text field(s) differ, e.g.
  // `company: "Acme Inc" vs "Acme, Inc."`. Absent for exact dups.
  diff?: string;
}

export interface AmbiguityFlag {
  column: string;
  kind: "date_order" | "mixed_type" | "coerce_failed";
  detail: string;
}

export interface ColumnContract {
  name: string;
  raw_name: string;
  dtype: string; // numeric | date | boolean | text
  role: string; // id | dimension | measure | timestamp | text
  meaning: string;
  confidence: number; // 0..1
  provisional: boolean;
  clarifying_question: string | null;
  is_id: boolean;
  is_fk: boolean;
  sample_values: (string | number | null)[];
}

export interface TableContract {
  name: string;
  summary: string;
  row_count: number;
  columns: ColumnContract[];
}

export interface RelationshipEdge {
  from_table: string;
  from_col: string;
  to_table: string;
  to_col: string;
  confidence: number;
  confidence_label: "high" | "medium" | "low";
  provisional: boolean;
  // exactly one edge per connected table-pair is active; only the active link is
  // used at query time (nl2sql prompt + guardrail whitelist).
  active?: boolean;
}

export interface SchemaContract {
  tables: TableContract[];
  relationships: RelationshipEdge[];
}

export type ChartHint = "single_value" | "bar" | "line" | "pie" | "scatter" | "table";

export interface AnswerResult {
  status: "answered" | "clarify" | "refused" | "blocked" | "error";
  question: string;
  insight: string | null;
  sql: string | null;
  assumptions: string[];
  followups: string[];
  clarifying_question: string | null;
  // helpful real-schema questions returned with a "couldn't map" refusal
  suggestions?: string[];
  // on a clarify, the concrete question a "Yes — run it" chip re-submits
  proposed_action?: string | null;
  blocked_reason: string | null;
  chart_hint: ChartHint | null;
  columns: string[];
  rows: Record<string, string | number | null>[];
  tables_used: string[];
  // present on error / fallback responses
  error?: string | null;
  error_kind?: string | null;
  provider_used?: string | null;
  fallback_note?: string | null;
}

export interface Metrics {
  queries_answered: number;
  destructive_blocked_pct: number;
  schema_accuracy_pct: number;
  bytes_retained: number;
}
