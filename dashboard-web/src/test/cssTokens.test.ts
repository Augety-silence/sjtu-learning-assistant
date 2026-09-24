import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");
const aiSectionMarker = "/* AI Chat workspace */";
const aiSection = css.slice(css.indexOf(aiSectionMarker));
const tailwindConfig = readFileSync(
  new URL("../../tailwind.config.js", import.meta.url),
  "utf8",
);
const buttonSource = readFileSync(
  new URL("../components/ui/Button.tsx", import.meta.url),
  "utf8",
);
const tabsSource = readFileSync(
  new URL("../components/ui/Tabs.tsx", import.meta.url),
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

  it("为深色主题覆盖核心界面与 AI 工作区语义色", () => {
    expect(css).toContain(':root[data-theme="dark"]');
    const darkTheme = css.slice(css.indexOf(':root[data-theme="dark"]'));
    for (const token of [
      "--surface-page: #17191d",
      "--text-primary: #f0f2f5",
      "--border-default: #59616d",
      "--action-primary: #6f9fff",
      "--surface-ai-elevated: #20242a",
      "--text-ai-content: #e3e7ed",
    ]) {
      expect(darkTheme).toContain(token);
    }
    expect(darkTheme).toContain("color-scheme: dark");
  });

  it("通知在浅色与深色主题中使用对应的表面和文字色", () => {
    const lightTheme = css.slice(0, css.indexOf(':root[data-theme="dark"]'));
    const darkTheme = css.slice(css.indexOf(':root[data-theme="dark"]'));
    expect(lightTheme).toContain("--surface-toast: #ffffff");
    expect(lightTheme).toContain("--text-toast: #1f2329");
    expect(darkTheme).toContain("--surface-toast: #25272b");
    expect(darkTheme).toContain("--text-toast: #f5f6f7");
    expect(css).toContain("background: var(--surface-toast)");
    expect(css).toContain("color: var(--text-toast)");
  });

  it("将 Tailwind 弱化文本映射到可访问语义色", () => {
    expect(css).toContain("--text-tertiary: #6b737d");
    expect(tailwindConfig).toContain('muted: "var(--text-tertiary)"');
    expect(tailwindConfig).not.toContain('muted: "#8f959e"');
  });

  it("AI 工作区及关联配置不再包含硬编码颜色", () => {
    expect(aiSection).toMatch(/^\/\* AI Chat workspace \*\//);
    expect(aiSection).not.toMatch(/#[0-9a-fA-F]{3,8}/);
    expect(tailwindConfig).not.toMatch(/#[0-9a-fA-F]{3,8}/);
    expect(buttonSource).not.toContain("bg-white");
    expect(tabsSource).not.toContain("bg-white");
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
