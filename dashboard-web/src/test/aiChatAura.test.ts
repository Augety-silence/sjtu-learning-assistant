import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(
  new URL("../ai-chat-aura.css", import.meta.url),
  "utf8",
);

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
      /\.ai-workspace::before \{[\s\S]*?pointer-events: none[\s\S]*?radial-gradient/,
    );
  });

  it("uses one user message surface owned by ai-markdown", () => {
    expect(css).toMatch(
      /\.ai-message-user \.ai-message-body \{[^}]*padding: 0[^}]*background: transparent[^}]*box-shadow: none/,
    );
    expect(css).toMatch(
      /\.ai-message-user \.ai-markdown \{[^}]*background: color-mix/,
    );
    expect(css).toMatch(
      /\.ai-message-user \.ai-markdown p,[\s\S]*?background: transparent[^}]*backdrop-filter: none/,
    );
  });

  it("keeps assistant reading, composer, and menus on distinct materials", () => {
    expect(css).toMatch(
      /\.ai-message-assistant \.ai-message-body \{[^}]*background: var\(--aura-reading\)[^}]*backdrop-filter: none/,
    );
    expect(css).toMatch(
      /\.ai-composer-box,[\s\S]*?background: var\(--aura-input\)[^}]*backdrop-filter: var\(--aura-blur-input\)/,
    );
    expect(css).toMatch(
      /\.ai-sidebar-agent-menu,[\s\S]*?background: var\(--aura-overlay\)[^}]*backdrop-filter: var\(--aura-blur-overlay\)/,
    );
    expect(css).not.toContain("background-size: 4px 4px");
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
  });
});
