/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1f2329",
        caption: "#646a73",
        muted: "#8f959e",
        line: "#dee0e3",
        component: "#d0d3d6",
        primary: "#1456f0",
        "primary-hover": "#336df4",
        "primary-pressed": "#0442d2",
        "surface-hover": "#f5f6f7",
        "neutral-hover": "#1f232914",
        "neutral-divider": "#1f232926",
        "control-fill": "#f2f3f5",
        danger: "#e22e28",
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
