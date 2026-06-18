"use client";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { ChartHint } from "@/lib/types";

// Varied shades across the gold accent family + neutral punctuation — never flat.
const PALETTE = [
  "#E8B339",
  "#F0C04A",
  "#B8862A",
  "#D4A03C",
  "#FFD884",
  "#8A8A93",
];

const FONT =
  'var(--font-sans), "Inter", system-ui, sans-serif';

// Axis ticks: our font, muted, no axis lines, no tick marks.
const axisProps = {
  tick: { fill: "#76767F", fontSize: 12, fontFamily: FONT },
  axisLine: false as const,
  tickLine: false as const,
  tickMargin: 10,
};

function fmtNum(v: any) {
  return typeof v === "number" ? v.toLocaleString() : v;
}

// High-contrast, themed tooltip — light text on a floating near-black card.
function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div
      style={{
        background: "rgba(10,10,13,0.96)",
        border: "1px solid rgba(246,246,244,0.16)",
        borderRadius: 12,
        padding: "10px 14px",
        boxShadow: "0 20px 40px -20px rgba(0,0,0,0.9)",
        fontFamily: FONT,
        backdropFilter: "blur(8px)",
      }}
    >
      {label !== undefined && (
        <div
          style={{
            color: "#F6F6F4",
            fontSize: 13,
            fontWeight: 600,
            marginBottom: 4,
          }}
        >
          {label}
        </div>
      )}
      {payload.map((p: any, i: number) => (
        <div
          key={i}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            color: "#C8C8CC",
            fontSize: 12.5,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 2,
              background: p.color || p.payload?.fill || "#E8B339",
            }}
          />
          <span>{p.name}</span>
          <span style={{ color: "#F6F6F4", fontWeight: 600, marginLeft: "auto" }}>
            {fmtNum(p.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

const gridProps = {
  strokeDasharray: "0",
  stroke: "rgba(246,246,244,0.05)",
  vertical: false,
};

export type ChartType = Exclude<ChartHint, "single_value">;

const legendProps = {
  wrapperStyle: { fontFamily: FONT, fontSize: 12, color: "#C8C8CC" },
  iconType: "circle" as const,
  iconSize: 9,
};

// A clear message in place of a chart we genuinely can't draw — never a blank canvas.
function ChartFallback({ text }: { text: string }) {
  return (
    <div className="flex h-[320px] w-full items-center justify-center rounded-xl border border-[var(--hairline)] bg-white/[0.02] px-8 text-center text-[13px] leading-relaxed text-graphite">
      {text}
    </div>
  );
}

// Pivot long rows (one row per x×series) into wide rows (one row per x, a column
// per series value) so a 2-dimension result can be drawn as grouped series.
function pivot(
  rows: Record<string, string | number | null>[],
  xKey: string,
  seriesKey: string,
  yKey: string,
) {
  const seriesVals: string[] = [];
  const byX = new Map<string, Record<string, string | number>>();
  for (const r of rows) {
    const xv = String(r[xKey] ?? "—");
    const sv = String(r[seriesKey] ?? "—");
    if (!seriesVals.includes(sv)) seriesVals.push(sv);
    if (!byX.has(xv)) byX.set(xv, { [xKey]: xv });
    const o = byX.get(xv)!;
    const n = typeof r[yKey] === "number" ? (r[yKey] as number) : 0;
    o[sv] = ((o[sv] as number) ?? 0) + n;
  }
  return { data: Array.from(byX.values()), seriesVals };
}

export default function ResultChart({
  type,
  rows,
  xKey,
  yKey,
  seriesKey,
}: {
  type: ChartType;
  rows: Record<string, string | number | null>[];
  xKey: string;
  yKey: string;
  seriesKey?: string;
}) {
  if (type === "table") return null;

  // ── Multi-dimension (two grouping columns) ───────────────────────────────────
  const grouped = !!seriesKey && seriesKey !== xKey;
  if (grouped) {
    // pie/scatter genuinely can't encode two dimensions → say so, don't draw blank.
    if (type === "pie" || type === "scatter") {
      return (
        <ChartFallback
          text={`A ${type} chart can't represent grouped data with two dimensions (${xKey} × ${seriesKey}). Try the bar chart or the table view.`}
        />
      );
    }
    const { data, seriesVals } = pivot(rows, xKey, seriesKey!, yKey);
    return (
      <div className="h-[320px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          {type === "line" ? (
            <LineChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey={xKey} {...axisProps} />
              <YAxis {...axisProps} width={56} tickFormatter={fmtNum} />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: "rgba(232,179,57,0.35)" }} />
              <Legend {...legendProps} />
              {seriesVals.map((sv, i) => (
                <Line
                  key={sv}
                  type="monotone"
                  dataKey={sv}
                  name={sv}
                  stroke={PALETTE[i % PALETTE.length]}
                  strokeWidth={2.5}
                  dot={{ r: 3, fill: PALETTE[i % PALETTE.length], strokeWidth: 0 }}
                  activeDot={{ r: 6 }}
                />
              ))}
            </LineChart>
          ) : (
            <BarChart data={data} margin={{ top: 12, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey={xKey} {...axisProps} />
              <YAxis {...axisProps} width={56} tickFormatter={fmtNum} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(232,179,57,0.08)" }} />
              <Legend {...legendProps} />
              {seriesVals.map((sv, i) => (
                <Bar
                  key={sv}
                  dataKey={sv}
                  name={sv}
                  fill={PALETTE[i % PALETTE.length]}
                  radius={[6, 6, 0, 0]}
                  maxBarSize={48}
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    );
  }

  // ── Single-dimension (unchanged) ─────────────────────────────────────────────
  return (
    <div className="h-[320px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        {type === "bar" ? (
          <BarChart data={rows} margin={{ top: 12, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey={xKey} {...axisProps} />
            <YAxis {...axisProps} width={56} tickFormatter={fmtNum} />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(232,179,57,0.08)" }} />
            <Bar dataKey={yKey} radius={[8, 8, 0, 0]} maxBarSize={64}>
              {rows.map((_, i) => (
                <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
              ))}
            </Bar>
          </BarChart>
        ) : type === "line" ? (
          <LineChart data={rows} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="lineStroke" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#E8B339" />
                <stop offset="100%" stopColor="#F0C04A" />
              </linearGradient>
            </defs>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey={xKey} {...axisProps} />
            <YAxis {...axisProps} width={56} tickFormatter={fmtNum} />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: "rgba(232,179,57,0.35)" }} />
            <Line
              type="monotone"
              dataKey={yKey}
              stroke="url(#lineStroke)"
              strokeWidth={3}
              dot={{ fill: "#E8B339", r: 4, strokeWidth: 0 }}
              activeDot={{ r: 7, fill: "#F0C04A", stroke: "#0A0A0B", strokeWidth: 3 }}
            />
          </LineChart>
        ) : type === "pie" ? (
          <PieChart>
            <Tooltip content={<ChartTooltip />} />
            <Pie
              data={rows}
              dataKey={yKey}
              nameKey={xKey}
              cx="50%"
              cy="50%"
              outerRadius={120}
              innerRadius={62}
              paddingAngle={3}
              stroke="#0A0A0D"
              strokeWidth={3}
            >
              {rows.map((_, i) => (
                <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
              ))}
            </Pie>
          </PieChart>
        ) : (
          <ScatterChart margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey={xKey} {...axisProps} />
            <YAxis dataKey={yKey} {...axisProps} width={56} tickFormatter={fmtNum} />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: "rgba(232,179,57,0.25)" }} />
            <Scatter data={rows}>
              {rows.map((_, i) => (
                <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
              ))}
            </Scatter>
          </ScatterChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
