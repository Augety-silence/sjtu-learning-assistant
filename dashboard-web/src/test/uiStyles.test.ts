import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");

describe("responsive and motion regression rules", () => {
  it("keeps long headings resilient", () => {
    expect(css).toMatch(/\.page-heading h1 \{[^}]*overflow-wrap: anywhere/);
    expect(css).toMatch(
      /\.message-dialog-header h3 \{[^}]*overflow-wrap: anywhere/,
    );
  });

  it("uses a full-height flex shell, a local workspace scroller and sticky header", () => {
    expect(css).toMatch(
      /\.app-shell \{[^}]*height: 100dvh[^}]*display: flex[^}]*overflow: hidden/,
    );
    expect(css).toMatch(/\.workspace \{[^}]*flex: 1[^}]*overflow-y: auto/);
    expect(css).toMatch(
      /\.page-header \{[^}]*position: sticky[^}]*top: 0[^}]*z-index:/,
    );
    expect(css.match(/\.workspace \{[^}]*\}/)?.[0]).not.toContain("max-width");
  });

  it("keeps narrow deadline tables reachable and the summary strip compact", () => {
    expect(css).toMatch(/\.table-surface \{[^}]*overflow-x: auto/);
    expect(css).toMatch(/\.table-surface table \{[^}]*min-width: 760px/);
    expect(css).toMatch(
      /@media \(max-width: 599px\)[\s\S]*?\.summary-strip \{[^}]*repeat\(3, minmax\(0, 1fr\)\)/,
    );
  });

  it("shares interaction states and contains no gradients", () => {
    for (const selector of [
      ".nav-item",
      ".list-row-button",
      ".tree-row",
      ".finder-row",
      ".assignment-card",
      ".pan-row",
      ".interactive-table-row",
    ]) {
      expect(css).toContain(selector);
    }
    expect(css).toContain("background: var(--surface-hover)");
    expect(css).toContain("background: var(--surface-selected)");
    expect(css).not.toMatch(/(?:linear|radial|conic)-gradient\s*\(/i);
  });

  it("reduces both animations and transitions", () => {
    const reducedMotion = css.match(
      /@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/,
    )?.[1];
    expect(reducedMotion).toContain("animation-duration: 0.01ms !important");
    expect(reducedMotion).toContain("transition-duration: 0.01ms !important");
    expect(reducedMotion).toContain("transition-delay: 0ms !important");
  });
});
