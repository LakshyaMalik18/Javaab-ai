// All mock data for Phase 5A. Shapes match lib/types.ts (= backend contracts),
// so Phase 5B replaces these constants with live API responses unchanged.
import type {
  AnswerResult,
  ChangeRecord,
  DuplicateGroup,
  AmbiguityFlag,
  SchemaContract,
} from "./types";

// ── The "mess" (raw, ugly) — used by the Clean set-piece (before) ────────────
export const RAW_ROWS: (string | number | null)[][] = [
  ["cst_id", "name", "country", "amt", "ord_dt", "status"],
  ["1001", " Acme Inc ", "USA", "$1,240.00", "03/04/2024", "Paid"],
  ["1002", "Acme, Inc.", "U.S.A.", "(320.50)", "13/04/2024", "paid"],
  ["1003", "Globex", "America", "€2.1k", "NA", "PAID"],
  ["1004", "Initech", "United States", "-", "21/05/2024", "Refunded"],
  ["1005", "Hooli", "uk", "£980", "07/06/2024", "n/a"],
  ["", "", "", "", "", ""],
  ["Total", "", "", "$4,540", "", ""],
];

// ── The "clean" (resolved) — used by the Clean set-piece (after) ─────────────
export const CLEAN_ROWS: (string | number | null)[][] = [
  ["customer_id", "name", "country", "amount", "order_date", "status"],
  [1001, "Acme Inc", "United States", 1240.0, "2024-04-03", "paid"],
  [1002, "Acme Inc", "United States", -320.5, "2024-04-13", "paid"],
  [1003, "Globex", "United States", 2100.0, null, "paid"],
  [1004, "Initech", "United States", null, "2024-05-21", "refunded"],
  [1005, "Hooli", "United Kingdom", 980.0, "2024-06-07", null],
];

// ── Cleaning report — ChangeLedger ───────────────────────────────────────────
export const MOCK_LEDGER: ChangeRecord[] = [
  {
    table: "orders",
    column: "amount",
    rule: "Currency & number normalization",
    cells_affected: 5,
    before_sample: ["$1,240.00", "(320.50)", "€2.1k", "£980"],
    after_sample: [1240.0, -320.5, 2100.0, 980.0],
    reversible: true,
  },
  {
    table: "orders",
    column: "order_date",
    rule: "Date format voting → ISO 8601",
    cells_affected: 4,
    before_sample: ["03/04/2024", "13/04/2024", "21/05/2024"],
    after_sample: ["2024-04-03", "2024-04-13", "2024-05-21"],
    reversible: true,
  },
  {
    table: "orders",
    column: "country",
    rule: "Canonicalization (USA / U.S.A. / America → United States)",
    cells_affected: 4,
    before_sample: ["USA", "U.S.A.", "America", "uk"],
    after_sample: ["United States", "United States", "United States", "United Kingdom"],
    reversible: true,
  },
  {
    table: "orders",
    column: "status",
    rule: "Case & value unification",
    cells_affected: 4,
    before_sample: ["Paid", "PAID", "Refunded"],
    after_sample: ["paid", "paid", "refunded"],
    reversible: true,
  },
  {
    table: "orders",
    column: "amount",
    rule: "Null token detection (NA, -, n/a → NULL)",
    cells_affected: 3,
    before_sample: ["NA", "-", "n/a"],
    after_sample: [null, null, null],
    reversible: true,
  },
  {
    table: "orders",
    column: "name",
    rule: "Whitespace trim + collapse",
    cells_affected: 2,
    before_sample: [" Acme Inc ", "Initech "],
    after_sample: ["Acme Inc", "Initech"],
    reversible: true,
  },
  {
    table: "orders",
    column: "—",
    rule: "Dropped empty + junk trailing rows ('Total' footer)",
    cells_affected: 2,
    before_sample: ["(blank row)", "Total …"],
    after_sample: ["(removed)", "(removed)"],
    reversible: true,
  },
];

export const MOCK_CELLS_CLEANED = MOCK_LEDGER.reduce(
  (n, r) => n + r.cells_affected,
  0,
);

export const MOCK_DUPLICATES: DuplicateGroup[] = [
  {
    kind: "near",
    row_indices: [0, 1],
    sample: { customer_id: "1001 / 1002", name: "Acme Inc vs Acme, Inc." },
  },
];

export const MOCK_AMBIGUITY: AmbiguityFlag[] = [
  {
    column: "order_date",
    kind: "date_order",
    detail:
      "Resolved DD/MM by voting — value 13/04 forced day-first for the whole column.",
  },
];

// ── Schema contract (customers + orders) ─────────────────────────────────────
export const MOCK_SCHEMA: SchemaContract = {
  tables: [
    {
      name: "customers",
      summary: "One row per customer account, keyed by customer_id.",
      row_count: 480,
      columns: [
        {
          name: "customer_id",
          raw_name: "cst_id",
          dtype: "numeric",
          role: "id",
          meaning: "Unique identifier for each customer.",
          confidence: 0.97,
          provisional: false,
          clarifying_question: null,
          is_id: true,
          is_fk: false,
          sample_values: [1001, 1002, 1003],
        },
        {
          name: "name",
          raw_name: "cust_name",
          dtype: "text",
          role: "dimension",
          meaning: "Customer company name.",
          confidence: 0.93,
          provisional: false,
          clarifying_question: null,
          is_id: false,
          is_fk: false,
          sample_values: ["Acme Inc", "Globex", "Hooli"],
        },
        {
          name: "country",
          raw_name: "ctry",
          dtype: "text",
          role: "dimension",
          meaning: "Customer billing country (canonicalized).",
          confidence: 0.9,
          provisional: false,
          clarifying_question: null,
          is_id: false,
          is_fk: false,
          sample_values: ["United States", "United Kingdom"],
        },
        {
          name: "tier",
          raw_name: "tr",
          dtype: "text",
          role: "dimension",
          meaning: "Account tier — likely subscription level, but values are coded.",
          confidence: 0.41,
          provisional: true,
          clarifying_question:
            "`tr`: is this the subscription tier (A/B/C) or a region code?",
          is_id: false,
          is_fk: false,
          sample_values: ["A", "B", "C"],
        },
      ],
    },
    {
      name: "orders",
      summary: "One row per order with amount, date and payment status.",
      row_count: 5120,
      columns: [
        {
          name: "order_id",
          raw_name: "ord_id",
          dtype: "numeric",
          role: "id",
          meaning: "Unique identifier for each order.",
          confidence: 0.98,
          provisional: false,
          clarifying_question: null,
          is_id: true,
          is_fk: false,
          sample_values: [50001, 50002, 50003],
        },
        {
          name: "customer_id",
          raw_name: "cst_id",
          dtype: "numeric",
          role: "id",
          meaning: "References the customer who placed the order.",
          confidence: 0.95,
          provisional: false,
          clarifying_question: null,
          is_id: false,
          is_fk: true,
          sample_values: [1001, 1002, 1003],
        },
        {
          name: "amount",
          raw_name: "amt",
          dtype: "numeric",
          role: "measure",
          meaning: "Order value in the account currency.",
          confidence: 0.88,
          provisional: false,
          clarifying_question: null,
          is_id: false,
          is_fk: false,
          sample_values: [1240.0, -320.5, 2100.0],
        },
        {
          name: "order_date",
          raw_name: "ord_dt",
          dtype: "date",
          role: "timestamp",
          meaning: "Date the order was placed (ISO 8601).",
          confidence: 0.86,
          provisional: false,
          clarifying_question: null,
          is_id: false,
          is_fk: false,
          sample_values: ["2024-04-03", "2024-04-13"],
        },
        {
          name: "status",
          raw_name: "status",
          dtype: "text",
          role: "dimension",
          meaning: "Payment status of the order.",
          confidence: 0.84,
          provisional: false,
          clarifying_question: null,
          is_id: false,
          is_fk: false,
          sample_values: ["paid", "refunded"],
        },
      ],
    },
  ],
  relationships: [
    {
      from_table: "orders",
      from_col: "customer_id",
      to_table: "customers",
      to_col: "customer_id",
      confidence: 0.96,
      confidence_label: "high",
      provisional: false,
    },
  ],
};

// ── The headline answer (executive order) ────────────────────────────────────
export const MOCK_ANSWER: AnswerResult = {
  status: "answered",
  question: "Which countries drove the most revenue last quarter?",
  insight:
    "The United States led last quarter with $642K — driving 68% of total revenue across 312 paid orders. The United Kingdom followed at $214K. Refunds were concentrated in two enterprise accounts and dragged net revenue down 4%.",
  sql: `SELECT c.country,
       SUM(o.amount) AS revenue,
       COUNT(*)      AS orders
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
WHERE o.status = 'paid'
  AND o.order_date >= '2024-04-01'
  AND o.order_date <  '2024-07-01'
GROUP BY c.country
ORDER BY revenue DESC
LIMIT 500;`,
  assumptions: [
    "Assumed 'last quarter' = Apr–Jun 2024 (previous calendar quarter).",
    "Revenue counts paid orders only; refunds excluded.",
  ],
  followups: [
    "Break United States revenue down by month",
    "Which 5 customers drove the most revenue?",
    "How did refunds trend across the quarter?",
  ],
  clarifying_question: null,
  blocked_reason: null,
  chart_hint: "bar",
  columns: ["country", "revenue", "orders"],
  rows: [
    { country: "United States", revenue: 642000, orders: 312 },
    { country: "United Kingdom", revenue: 214000, orders: 118 },
    { country: "Germany", revenue: 138500, orders: 76 },
    { country: "Canada", revenue: 96200, orders: 54 },
    { country: "Australia", revenue: 61800, orders: 39 },
  ],
  tables_used: ["orders", "customers"],
};

// A blocked example for the guardrail card
export const MOCK_BLOCKED: AnswerResult = {
  status: "blocked",
  question: "delete all refunded orders",
  insight: null,
  sql: "DELETE FROM orders WHERE status = 'refunded';",
  assumptions: [],
  followups: [
    "How many orders were refunded?",
    "Show refunded orders by customer",
  ],
  clarifying_question: null,
  blocked_reason:
    "This is a DELETE statement. Javaab only runs read-only SELECT queries — your data is never modified.",
  chart_hint: null,
  columns: [],
  rows: [],
  tables_used: ["orders"],
};

// Time-series + categorical sample for chart-type switching in chat
export const MOCK_TREND = [
  { month: "Apr", revenue: 198000 },
  { month: "May", revenue: 221000 },
  { month: "Jun", revenue: 223000 },
];
