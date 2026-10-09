import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(
  new URL("../ai-chat-aura.css", import.meta.url),
  "utf8",
);

describe("AI Chat Aura workspace", () => {
  it("keeps conversation, navigation, and activity as separate glass panes", () => {
    expect(css).toContain(".ai-sidebar,");
    expect(css).toContain(".ai-conversation,");
    expect(css).toContain(".ai-activity {");
    expect(css).toContain("blur(24px) saturate(1.14)");
  });

  it("uses strong glass and white noise only on transient AI surfaces", () => {
    expect(css).toContain(".ai-composer-box,");
    expect(css).toContain(".ai-sidebar-agent-menu,");
    expect(css).toContain("blur(38px) saturate(1.32)");
    expect(css).toContain("background-size: 4px 4px, 7px 7px");
  });

  it("preserves responsive and reduced-transparency fallbacks", () => {
    expect(css).toContain("@media (max-width: 800px)");
    expect(css).toContain("@media (prefers-reduced-transparency: reduce)");
    expect(css).toContain("background: var(--aura-backdrop-solid)");
    expect(css).toContain("backdrop-filter: none");
  });
});
