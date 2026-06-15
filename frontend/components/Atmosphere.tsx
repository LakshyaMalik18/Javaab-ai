"use client";
// Slow aurora/mesh gradients drifting in deep obsidian space. Pure CSS transforms.
export default function Atmosphere({ dense = false }: { dense?: boolean }) {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      <div
        className="aurora-blob animate-aurora"
        style={{
          width: 620,
          height: 620,
          top: "-12%",
          left: "-8%",
          background:
            "radial-gradient(circle at 30% 30%, rgba(232,179,57,0.55), transparent 60%)",
        }}
      />
      <div
        className="aurora-blob animate-aurora-slow"
        style={{
          width: 540,
          height: 540,
          bottom: "-14%",
          right: "-6%",
          background:
            "radial-gradient(circle at 60% 40%, rgba(240,192,74,0.35), transparent 60%)",
        }}
      />
      {dense && (
        <div
          className="aurora-blob animate-aurora"
          style={{
            width: 420,
            height: 420,
            top: "40%",
            left: "55%",
            opacity: 0.3,
            background:
              "radial-gradient(circle at 50% 50%, rgba(240,192,74,0.22), transparent 60%)",
          }}
        />
      )}
    </div>
  );
}
