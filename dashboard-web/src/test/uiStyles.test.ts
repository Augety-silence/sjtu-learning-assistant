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

  it("keeps narrow deadline tables horizontally reachable and KPIs compact", () => {
    expect(css).toMatch(/\.table-surface \{[^}]*overflow-x: auto/);
    expect(css).toMatch(/\.table-surface table \{[^}]*min-width: 760px/);
    expect(css).toMatch(
      /@media \(max-width: 599px\)[\s\S]*?\.kpi-grid \{[^}]*repeat\(3, minmax\(0, 1fr\)\)/,
    );
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
