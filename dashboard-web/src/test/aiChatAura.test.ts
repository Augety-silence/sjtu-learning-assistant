import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(
  new URL("../ai-chat-aura.css", import.meta.url),
  "utf8",
);
const tokens = readFileSync(new URL("../index.css", import.meta.url), "utf8");

describe("AI Chat Aura workspace", () => {
  it("keeps the outer shell and conversation stream free of duplicate backdrops", () => {
    expect(css).toMatch(
      /\.ai-workspace \{[\s\S]*?background: var\(--aura-canvas\)[^}]*backdrop-filter: none/,
    );
    expect(css).toMatch(
      /\.ai-chat-thread \{[^}]*background: transparent[^}]*backdrop-filter: none/,
    );
    expect(css).toMatch(/\.ai-chat-messages \{[^}]*background: transparent/);
    expect(css).toMatch(
      /\.ai-workspace::before \{[\s\S]*?opacity: 1[\s\S]*?pointer-events: none[\s\S]*?background-image: var\(--aura-scene-background\)[\s\S]*?filter: var\(--aura-scene-filter\)/,
    );
    const sceneStart = tokens.indexOf("--aura-scene-background:");
    const sceneEnd = tokens.indexOf("--aura-panel:", sceneStart);
    const sceneBackground = tokens.slice(sceneStart, sceneEnd);
    expect(sceneBackground.match(/radial-gradient\(/g)).toHaveLength(3);
    expect(sceneBackground).toContain("var(--aura-scene-image)");
    expect(css).toMatch(
      /\.ai-sidebar,[\s\S]*?border-radius: var\(--aura-radius-window\)/,
    );
  });

  it("uses one user message surface owned by ai-markdown", () => {
    expect(css).toMatch(
      /\.ai-message-user \.ai-message-body \{[^}]*padding: 0[^}]*background: transparent[^}]*box-shadow: none/,
    );
    expect(css).toMatch(
      /\.ai-message-user \.ai-markdown \{[\s\S]*?var\(--glass-user-surface\) padding-box[\s\S]*?backdrop-filter: blur\(14px\) saturate\(1\.12\)/,
    );
    expect(css).toMatch(
      /\.ai-message-user \.ai-markdown p,[\s\S]*?background: transparent[^}]*backdrop-filter: none/,
    );
  });

  it("keeps assistant text flat while composer and menus retain purposeful materials", () => {
    expect(css).toMatch(
      /\.ai-message-assistant \.ai-message-body \{[^}]*background: transparent[^}]*box-shadow: none[^}]*backdrop-filter: none/,
    );
    expect(css).toMatch(
      /\.ai-composer-box,[\s\S]*?var\(--glass-floating-surface\) padding-box[\s\S]*?backdrop-filter: var\(--glass-floating-filter\)/,
    );
    expect(css).not.toContain(".ai-composer::before");
    expect(css).toMatch(
      /\.ai-composer \.ai-composer-input \{[^}]*appearance: none[^}]*-webkit-appearance: none[^}]*background: transparent[^}]*box-shadow: none/,
    );
    expect(css).toMatch(
      /\.ai-sidebar-agent-menu,[\s\S]*?var\(--glass-floating-surface\) padding-box[\s\S]*?backdrop-filter: var\(--glass-floating-filter\)/,
    );
    expect(css).not.toContain("background-size: 4px 4px");
  });

  it("uses one 760px conversation axis and an inline execution disclosure", () => {
    expect(css).toContain("--ai-content-width: 760px");
    expect(css).toMatch(
      /\.ai-chat-messages \{[^}]*width: min\(var\(--ai-content-width\), calc\(100% - 48px\)\)/,
    );
    expect(css).toMatch(
      /\.ai-composer \{[^}]*width: min\(var\(--ai-content-width\), calc\(100% - 48px\)\)/,
    );
    expect(css).toMatch(/\.ai-execution-card > summary \{/);
    expect(css).toMatch(
      /\.ai-execution-card \{[^}]*width: min\(100%, 560px\)[\s\S]*?var\(--glass-card-surface\) padding-box/,
    );
    expect(css).toMatch(/\.ai-message-user \{[^}]*justify-content: flex-end/);
    expect(css).toMatch(
      /\.ai-execution-detail \.ai-tool-runs \{[^}]*max-height: min\(300px, 38vh\)[^}]*overflow-y: auto/,
    );
  });

  it("uses a dedicated content-width Activity toggle instead of positional targeting", () => {
    expect(css).toMatch(
      /\.ai-activity-toggle \{[^}]*width: max-content[^}]*justify-self: end/,
    );
    expect(css).not.toContain(".ai-conversation-header > button:last-child");
    expect(css).toMatch(
      /@media \(min-width: 1050px\)[\s\S]*?grid-template-columns: minmax\(0, 1fr\) auto/,
    );
  });

  it("preserves responsive and accessibility fallbacks", () => {
    expect(css).toMatch(
      /@media \(max-width: 1049px\)[\s\S]*?grid-template-columns: minmax\(0, 1fr\)/,
    );
    expect(css).toContain("@media (max-width: 800px)");
    expect(css).toContain("@media (prefers-reduced-transparency: reduce)");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("background: var(--aura-backdrop-solid)");
    expect(css).toContain("backdrop-filter: none");
    expect(css).toContain("overflow-x: auto");
    expect(css).toContain("--glass-base-filter");
    expect(css).toContain("--glass-card-filter");
    expect(css).toContain("--glass-floating-filter");
  });
});
