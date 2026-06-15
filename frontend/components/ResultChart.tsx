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

export default function ResultChart({
  type,
  rows,
  xKey,
  yKey,
}: {
  type: ChartType;
  rows: Record<string, string | number | null>[];
  xKey: string;
  yKey: string;
}) {
  if (type === "table") return null;

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
