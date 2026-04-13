/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          0: "#09090b",
          1: "#111114",
          2: "#18181b",
          3: "#1f1f23",
          4: "#27272a",
        },
        accent: {
          DEFAULT: "#6366f1",
          hover: "#818cf8",
          muted: "#4f46e5",
        },
        status: {
          running: "#22c55e",
          awaiting: "#f59e0b",
          failed: "#ef4444",
          completed: "#6366f1",
          created: "#64748b",
          escalated: "#f97316",
          cancelled: "#6b7280",
        },
        agent: {
          planner: "#8b5cf6",
          bootstrapper: "#06b6d4",
          worker: "#22c55e",
          reviewer: "#f59e0b",
          orchestrator: "#6366f1",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      animation: {
        "pulse-dot": "pulse-dot 1.5s ease-in-out infinite",
      },
      keyframes: {
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.3" },
        },
      },
    },
  },
  plugins: [],
};
