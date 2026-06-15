import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // editorial B&W base with depth + one accent
        void: "#07070A",
        obsidian: {
          DEFAULT: "#0A0A0D",
          800: "#0F0F13",
          700: "#16161B",
          600: "#1D1D23",
          500: "#26262D",
        },
        ink: {
          DEFAULT: "#F6F6F4",
          dim: "#C8C8CC",
        },
        graphite: {
          DEFAULT: "#76767F",
          dim: "#4A4A52",
        },
        // class keys kept as `indigo`/`amber`; values are gold — the single accent
        indigo: {
          glow: "#E8B339", // the single accent (gold)
          soft: "#F0C04A",
          deep: "#B8862A",
        },
        amber: {
          warm: "#F0C04A", // the answer/insight — same gold warmth
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "system-ui", "sans-serif"],
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
      },
      fontFeatureSettings: {
        tabular: '"tnum" 1',
      },
      boxShadow: {
        glow: "0 0 40px -8px rgba(232,179,57,0.5)",
        "glow-amber": "0 0 40px -8px rgba(240,192,74,0.4)",
        glass: "0 8px 40px -12px rgba(0,0,0,0.6)",
        float: "0 30px 60px -30px rgba(0,0,0,0.8), 0 2px 10px -4px rgba(0,0,0,0.5)",
      },
      backgroundImage: {
        "obsidian-fade":
          "linear-gradient(180deg, #0B0B0C 0%, #101013 50%, #0B0B0C 100%)",
      },
      keyframes: {
        aurora: {
          "0%, 100%": { transform: "translate3d(0,0,0) scale(1)" },
          "50%": { transform: "translate3d(4%,-3%,0) scale(1.15)" },
        },
        grain: {
          "0%,100%": { transform: "translate(0,0)" },
          "25%": { transform: "translate(-2%,1%)" },
          "50%": { transform: "translate(1%,-2%)" },
          "75%": { transform: "translate(2%,2%)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        aurora: "aurora 18s ease-in-out infinite",
        "aurora-slow": "aurora 26s ease-in-out infinite",
        grain: "grain 8s steps(4) infinite",
      },
    },
  },
  plugins: [],
};

export default config;
