import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");
const tailwindConfig = readFileSync(
  new URL("../../tailwind.config.js", import.meta.url),
  "utf8",
);

describe("CSS semantic tokens", () => {
  it("定义完整语义色源且不再使用半像素边界", () => {
    for (const token of [
      "--surface-page",
      "--text-primary",
      "--border-default",
      "--action-primary",
      "--status-danger",
      "--focus-ring",
    ]) {
      expect(css).toContain(token);
    }
    expect(css).not.toContain("0.5px");
  });

  it("将 Tailwind 弱化文本映射到可访问语义色", () => {
    expect(css).toContain("--text-tertiary: #6b737d");
    expect(tailwindConfig).toContain('muted: "var(--text-tertiary)"');
    expect(tailwindConfig).not.toContain('muted: "#8f959e"');
  });

  it("消息正文显式允许文本选择复制", () => {
    expect(css).toContain("user-select: text");
  });

  it("关键移动热区与紧凑 KPI 使用 599px 断点", () => {
    expect(css).toContain("@media (max-width: 599px)");
    expect(css).toContain("grid-template-columns: repeat(3, minmax(0, 1fr))");
    expect(css).toContain("min-height: 44px");
  });
});
