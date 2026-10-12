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
    const sceneStart = tokens.indexOf("--aura-scene-background:");
    const sceneEnd = tokens.indexOf("--aura-panel:", sceneStart);
    const sceneBackground = tokens.slice(sceneStart, sceneEnd);
    expect(sceneBackground.match(/radial-gradient\(/g)).toHaveLength(3);
    expect(sceneBackground).toContain("var(--aura-scene-image)");
    expect(tokens).toContain('url("./assets/mist-blue-v1.png")');
    expect(atmosphere).toContain("filter: var(--aura-scene-filter)");
    expect(atmosphere).toContain(
      "background-image: var(--aura-scene-background)",
    );
    expect(atmosphere).toContain(
      "background-size: 100% 100%, auto, auto, auto, cover",
    );
    expect(tokens).toContain("--aura-scene-filter: blur(3px) saturate(0.94)");
    expect(atmosphere).toContain("opacity: 1");
    expect(atmosphere).toContain("pointer-events: none");
    expect(css).toMatch(
      /\.app-shell\[data-aura-shell\] \{[^}]*background: transparent[^}]*backdrop-filter: none/,
    );
    expect(css).toMatch(
      /\.workspace\[data-aura-surface="workspace"\] \{[^}]*background: transparent[^}]*backdrop-filter: none/,
    );
  });

  it("uses one weakly blurred reading surface without blurring rows or table cells", () => {
    expect(css).toMatch(
      /\.overview-panel,[\s\S]*?background: var\(--aura-reading\)[^}]*backdrop-filter: var\(--aura-blur-reading\)/,
    );
    expect(tokens).toContain("--aura-blur-reading: blur(16px) saturate(1.06)");
    expect(css).toMatch(
      /\.overview-panel \.list-surface,[\s\S]*?\.table-surface td \{[^}]*background: transparent[^}]*backdrop-filter: none/,
    );
    expect(css).toContain(".overview-dashboard .summary-item:hover");
    expect(css).toContain("transform: none");
  });

  it("aligns page lists with the shared workspace gutter", () => {
    expect(css).toMatch(
      /\.content \{[^}]*padding: var\(--aura-content-gap\) 0 32px/,
    );
    expect(css).toMatch(
      /\.section-stack:not\(\.overview-dashboard\):not\(\.schedule-workspace\) \{[^}]*width: 100%[^}]*max-width: none[^}]*margin-inline: 0/,
    );
    expect(css).toMatch(
      /\.overview-dashboard,[\s\S]*?\.schedule-workspace \{[^}]*width: 100%[^}]*max-width: none[^}]*margin-inline: 0/,
    );
  });

  it("uses the same outer frame for the video toolbar, player, and recording list", () => {
    for (const token of [
      "--aura-window-gutter: 10px",
      "--aura-content-gap: 20px",
      "--aura-panel-padding: 20px",
      "--aura-video-list-width: 340px",
    ]) {
      expect(tokens).toContain(token);
    }
    expect(css).toMatch(
      /\.video-workbench \{[^}]*width: 100%[^}]*border: 0[^}]*background: transparent/,
    );
    expect(css).toMatch(
      /\.video-learning-grid \{[^}]*minmax\(0, 1fr\) minmax\(320px, var\(--aura-video-list-width\)\)[^}]*gap: var\(--aura-content-gap\)/,
    );
    expect(css).toMatch(/\.video-list-jump \{[^}]*display: none/);
    expect(css).toMatch(
      /@media \(max-width: 900px\)[\s\S]*?\.video-learning-grid \{[^}]*grid-template-columns: minmax\(0, 1fr\)/,
    );
  });

  it("shares compact label, filter, and select sizing without system select chrome", () => {
    for (const token of [
      "--compact-meta-height: 24px",
      "--compact-filter-height: 32px",
      "--compact-select-height: 36px",
      "--compact-radius: 8px",
    ]) {
      expect(tokens).toContain(token);
    }
    expect(tokens).toContain(".aura-compact-label");
    expect(tokens).toContain(".aura-filter-chip");
    expect(css).toMatch(
      /\.app-shell select:not\(\[multiple\]\)[\s\S]*?appearance: none[\s\S]*?background-image: url/,
    );
    expect(css).toContain("background-position: right 10px center");
  });

  it("uses one radius scale for windows, panels, toolbars, overlays and controls", () => {
    for (const token of [
      "--aura-radius-window: 22px",
      "--aura-radius-card: 16px",
      "--aura-radius-toolbar: 14px",
      "--aura-radius-overlay: 18px",
      "--aura-radius-control: 10px",
    ]) {
      expect(tokens).toContain(token);
    }
    expect(css).toContain("border-radius: var(--aura-radius-overlay)");
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
