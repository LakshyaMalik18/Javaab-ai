"use client";
// SET-PIECE 3 — "The Connection". Two tables slide in; a join edge DRAWS between
// orders.customer_id ↔ customers.customer_id with a containment glow; a pulse
// travels the edge. Self-contained SVG so it scales crisply at any size.
import { useRef } from "react";
import { motion, useInView } from "framer-motion";
import { MOCK_SCHEMA } from "@/lib/mock";
import type { RelationshipEdge } from "@/lib/types";

export default function JoinDrawSetPiece({
  edge = MOCK_SCHEMA.relationships[0],
  leftCols = ["order_id", "customer_id", "amount", "order_date", "status"],
  rightCols = ["customer_id", "name", "country", "tier"],
  leftTable = "orders",
  rightTable = "customers",
  fkRow = 1, // index of customer_id in leftCols
  pkRow = 0, // index of customer_id in rightCols
  auto = true,
}: {
  edge?: RelationshipEdge;
  leftCols?: string[];
  rightCols?: string[];
  leftTable?: string;
  rightTable?: string;
  fkRow?: number;
  pkRow?: number;
  auto?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-100px" });
  const start = auto && inView;

  const rowH = 30;
  const headH = 38;
  const cardW = 200;
  const W = 720;
  const H = 320;
  const leftX = 40;
  const rightX = W - cardW - 40;
  const topL = 60;
  const topR = 90;

  const fkY = topL + headH + fkRow * rowH + rowH / 2;
  const pkY = topR + headH + pkRow * rowH + rowH / 2;
  const x1 = leftX + cardW;
  const x2 = rightX;
  const midX = (x1 + x2) / 2;
  const path = `M ${x1} ${fkY} C ${midX} ${fkY}, ${midX} ${pkY}, ${x2} ${pkY}`;

  const pct = Math.round(edge.confidence * 100);

  return (
    <div ref={ref} className="w-full">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={`Join discovered between ${leftTable} and ${rightTable}`}
      >
        <defs>
          <linearGradient id="edgeGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#E8B339" />
            <stop offset="100%" stopColor="#F0C04A" />
          </linearGradient>
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* edge */}
        <motion.path
          d={path}
          fill="none"
          stroke="url(#edgeGrad)"
          strokeWidth={2.5}
          filter="url(#glow)"
          initial={{ pathLength: 0, opacity: 0 }}
          animate={start ? { pathLength: 1, opacity: 1 } : {}}
          transition={{ duration: 1.1, delay: 0.6, ease: [0.16, 1, 0.3, 1] }}
        />
        {/* traveling pulse */}
        {start && (
          <motion.circle
            r={4}
            fill="#F0C04A"
            filter="url(#glow)"
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 1, 1, 0] }}
            transition={{ duration: 1.6, delay: 1.5, repeat: Infinity, repeatDelay: 1 }}
          >
            <animateMotion dur="1.6s" begin="1.5s" repeatCount="indefinite" path={path} />
          </motion.circle>
        )}

        {/* left card */}
        <TableCard
          x={leftX}
          y={topL}
          w={cardW}
          headH={headH}
          rowH={rowH}
          title={leftTable}
          cols={leftCols}
          highlight={fkRow}
          highlightLabel="FK"
          start={start}
          delay={0}
        />
        {/* right card */}
        <TableCard
          x={rightX}
          y={topR}
          w={cardW}
          headH={headH}
          rowH={rowH}
          title={rightTable}
          cols={rightCols}
          highlight={pkRow}
          highlightLabel="PK"
          start={start}
          delay={0.2}
          fromRight
        />

        {/* confidence label on the edge */}
        <motion.g
          initial={{ opacity: 0 }}
          animate={start ? { opacity: 1 } : {}}
          transition={{ delay: 1.6 }}
        >
          <rect
            x={midX - 70}
            y={(fkY + pkY) / 2 - 14}
            width={140}
            height={28}
            rx={14}
            fill="#16161A"
            stroke="rgba(232,179,57,0.4)"
          />
          <text
            x={midX}
            y={(fkY + pkY) / 2 + 4}
            textAnchor="middle"
            fill="#F0C04A"
            fontSize={12}
            fontWeight={600}
          >
            {pct}% · value match
          </text>
        </motion.g>
      </svg>
    </div>
  );
}

function TableCard({
  x,
  y,
  w,
  headH,
  rowH,
  title,
  cols,
  highlight,
  highlightLabel,
  start,
  delay,
  fromRight = false,
}: {
  x: number;
  y: number;
  w: number;
  headH: number;
  rowH: number;
  title: string;
  cols: string[];
  highlight: number;
  highlightLabel: string;
  start: boolean;
  delay: number;
  fromRight?: boolean;
}) {
  const h = headH + cols.length * rowH;
  return (
    <motion.g
      initial={{ opacity: 0, x: fromRight ? 30 : -30 }}
      animate={start ? { opacity: 1, x: 0 } : {}}
      transition={{ duration: 0.7, delay, ease: [0.16, 1, 0.3, 1] }}
    >
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={12}
        fill="#101013"
        stroke="rgba(244,244,242,0.1)"
      />
      <rect x={x} y={y} width={w} height={headH} rx={12} fill="rgba(232,179,57,0.08)" />
      <text x={x + 14} y={y + 24} fill="#F4F4F2" fontSize={13} fontWeight={600}>
        {title}
      </text>
      {cols.map((c, i) => {
        const cy = y + headH + i * rowH;
        const hot = i === highlight;
        return (
          <g key={c}>
            {hot && (
              <rect
                x={x + 4}
                y={cy + 3}
                width={w - 8}
                height={rowH - 4}
                rx={6}
                fill="rgba(240,192,74,0.1)"
                stroke="rgba(240,192,74,0.35)"
              />
            )}
            <text
              x={x + 14}
              y={cy + rowH / 2 + 4}
              fill={hot ? "#F0C04A" : "#8A8A93"}
              fontSize={12}
              fontWeight={hot ? 600 : 400}
            >
              {c}
            </text>
            {hot && (
              <text
                x={x + w - 14}
                y={cy + rowH / 2 + 4}
                textAnchor="end"
                fill="#F0C04A"
                fontSize={10}
                fontWeight={700}
              >
                {highlightLabel}
              </text>
            )}
          </g>
        );
      })}
    </motion.g>
  );
}
