/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Deep charcoal ops-console surface, layered for depth.
        ink: {
          950: "#070910",
          900: "#0b0e14",
          850: "#0f131c",
          800: "#141925",
          700: "#1c2231",
          600: "#272f42",
          500: "#3a445c",
        },
        // Signal palette — amber/red/green + a cool electric accent.
        signal: {
          red: "#ff5c5c",
          amber: "#ffb020",
          green: "#3ddc97",
          blue: "#4d9fff",
          violet: "#a78bfa",
        },
      },
      fontFamily: {
        mono: [
          "JetBrains Mono",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(77,159,255,0.25), 0 0 24px rgba(77,159,255,0.15)",
        panel: "0 1px 0 0 rgba(255,255,255,0.03), 0 12px 40px -12px rgba(0,0,0,0.7)",
      },
      keyframes: {
        pulseNode: {
          "0%,100%": { boxShadow: "0 0 0 0 rgba(77,159,255,0.55)" },
          "50%": { boxShadow: "0 0 0 10px rgba(77,159,255,0)" },
        },
        dash: {
          to: { strokeDashoffset: "-16" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        blink: {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.25" },
        },
      },
      animation: {
        pulseNode: "pulseNode 1.6s ease-out infinite",
        dash: "dash 0.6s linear infinite",
        shimmer: "shimmer 1.8s infinite",
        blink: "blink 1.1s steps(2,start) infinite",
      },
    },
  },
  plugins: [],
};
