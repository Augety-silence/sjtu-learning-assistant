/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "var(--text-primary)",
        caption: "var(--text-caption)",
        muted: "var(--text-tertiary)",
        inverse: "var(--text-inverse)",
        line: "var(--border-line)",
        component: "var(--border-component)",
        primary: "var(--action-primary)",
        "primary-hover": "var(--action-hover)",
        "primary-pressed": "var(--action-button-pressed)",
        "surface-elevated": "var(--surface-elevated)",
        "surface-hover": "var(--surface-control-hover)",
        "neutral-hover": "var(--surface-neutral-hover)",
        "neutral-divider": "var(--border-neutral)",
        "control-fill": "var(--surface-control)",
        danger: "var(--action-danger)",
      },
      borderRadius: { ud: "8px", control: "6px" },
      fontFamily: {
        sans: [
          "LarkCircular",
          "-apple-system",
          "BlinkMacSystemFont",
          "PingFang SC",
          "Hiragino Sans GB",
          "Microsoft YaHei",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
