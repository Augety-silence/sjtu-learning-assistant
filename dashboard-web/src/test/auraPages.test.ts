import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../aura-pages.css", import.meta.url), "utf8");
const tokens = readFileSync(new URL("../index.css", import.meta.url), "utf8");

describe("Aura page material system", () => {
  it("keeps shared navigation, reading, input, and overlay materials centralized", () => {
    for (const token of [
      "--aura-navigation",
      "--aura-header",
      "--aura-stat",
      "--aura-reading",
      "--aura-input",
      "--aura-overlay",
      "--aura-blur-navigation",
      "--aura-blur-overlay",
    ]) {
      expect(tokens).toContain(token);
    }
    expect(tokens).toContain(':root[data-theme="dark"]');
  });

  it("draws one continuous static atmosphere behind transparent shells", () => {
    const atmosphere = css.slice(
      css.indexOf(".app-shell[data-aura-shell]::before"),
      css.indexOf('.sidebar[data-aura-surface="navigation"]'),
    );
    expect(atmosphere.match(/radial-gradient\(/g)).toHaveLength(4);
    expect(atmosphere).toContain("filter: none");
    expect(atmosphere).toContain("opacity: 1");
    expect(atmosphere).toContain("pointer-events: none");
    expect(css).toMatch(
      /\.app-shell\[data-aura-shell\] \{[^}]*background: transparent[^}]*backdrop-filter: none/,
    );
    expect(css).toMatch(
      /\.workspace\[data-aura-surface="workspace"\] \{[^}]*background: transparent[^}]*backdrop-filter: none/,
    );
  });

  it("uses stable reading surfaces without blurring rows or table cells", () => {
    expect(css).toMatch(
      /\.overview-panel,[\s\S]*?background: var\(--aura-reading\)[^}]*backdrop-filter: none/,
    );
    expect(css).toMatch(
      /\.overview-panel \.list-surface,[\s\S]*?\.table-surface td \{[^}]*background: transparent[^}]*backdrop-filter: none/,
    );
    expect(css).toContain(".overview-dashboard .summary-item:hover");
    expect(css).toContain("transform: none");
  });

  it("shares one restrained overlay material across transient surfaces", () => {
    for (const selector of [
      ".toast",
      ".message-dialog",
      ".confirm-dialog",
      ".video-more-menu",
      ".material-move-menu",
      ".ai-personalization-panel",
    ]) {
      expect(css).toContain(selector);
    }
    expect(css).toContain("background: var(--aura-overlay)");
    expect(css).toContain("backdrop-filter: var(--aura-blur-overlay)");
    expect(css).not.toContain("background-size: 4px 4px");
  });

  it("provides reduced-transparency, reduced-motion, and no-blur fallbacks", () => {
    expect(css).toMatch(
      /@media \(max-width: 1199px\) and \(min-width: 600px\)[\s\S]*?width: 72px/,
    );
    expect(css).toContain("@media (prefers-reduced-transparency: reduce)");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain(
      "@supports not ((-webkit-backdrop-filter: blur(1px))",
    );
    expect(css).toContain("background: var(--aura-backdrop-solid)");
    expect(css).toContain("-webkit-backdrop-filter: none");
    expect(css).toContain("backdrop-filter: none");
  });
});
