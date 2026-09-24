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

  it("keeps the compact sidebar through 1199px", () => {
    expect(css).toMatch(
      /@media \(max-width: 1199px\) and \(min-width: 600px\)[\s\S]*?\.sidebar \{[^}]*width: 72px[^}]*flex-basis: 72px/,
    );
    expect(css).not.toContain(
      "@media (max-width: 1023px) and (min-width: 600px)",
    );
  });

  it("keeps the page header usable at 320px", () => {
    expect(css).toMatch(/\.page-heading > div \{[^}]*min-width: 0/);
    expect(css).toMatch(
      /@media \(max-width: 380px\)[\s\S]*?\.page-header \{[^}]*gap: 8px[\s\S]*?\.page-heading \{[^}]*gap: 8px[\s\S]*?\.page-heading p \{[^}]*display: none[\s\S]*?\.page-header > button \{[^}]*padding-inline: 10px/,
    );
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

  it("keeps toast chrome clean and pauses its progress feedback", () => {
    expect(css).toMatch(/\.toast \{[^}]*border: 0/);
    expect(css).toContain(".toast-progress");
    expect(css).toMatch(
      /\.toast\[data-paused="true"\] \.toast-progress \{[^}]*animation-play-state: paused/,
    );
  });

  it("keeps the AI personalization panel within the resizable sidebar", () => {
    const panelStart = css.indexOf(
      ".ai-personalization-panel " + String.fromCharCode(123),
    );
    expect(panelStart).toBeGreaterThan(-1);
    const panelRule = css.slice(
      panelStart,
      css.indexOf(String.fromCharCode(125), panelStart),
    );
    expect(panelRule).toContain("left: 8px");
    expect(panelRule).toContain("width: min(336px, calc(100% - 16px))");
  });

  it("keeps motion on compositor-friendly properties", () => {
    expect(css).toContain("--motion-panel: 220ms");
    expect(css).toContain("--ease-motion-out: cubic-bezier(0.16, 1, 0.3, 1)");
    expect(css).not.toMatch(/transition:\s*(?:grid-template-columns|width)/);
    expect(css).toContain("transform: scale(0.98)");
  });

  it("stops continuous loading motion when reduced motion is requested", () => {
    const reducedBlocks = Array.from(
      css.matchAll(
        /@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/g,
      ),
      (match) => match[1],
    ).join("\n");
    expect(reducedBlocks).toContain(".toast-progress");
    expect(reducedBlocks).toContain(".ai-thinking > span");
    expect(reducedBlocks).toContain("[data-motion-layer]");
    expect(reducedBlocks).toContain("[data-motion-surface]");
    expect(reducedBlocks).toContain(".status-running");
    expect(reducedBlocks).toContain("animation: none !important");
    expect(reducedBlocks).toContain("transform: none !important");
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
