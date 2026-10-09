import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../aura-pages.css", import.meta.url), "utf8");
const overlaySelectors = [
  ".toast",
  ".dialog-panel",
  ".confirm-dialog",
  ".config-dialog",
  ".video-course-popup",
  ".video-more-menu",
  ".video-player-popover",
  ".material-move-menu",
  ".ai-personalization-panel",
  ".ai-preset-menu",
];

function sectionBetween(startMarker: string, endMarker: string): string {
  const start = css.indexOf(startMarker);
  const end = css.indexOf(endMarker, start + startMarker.length);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  return css.slice(start, end);
}

describe("Aura page glass surfaces", () => {
  it("reserves strong glass and subtle white noise for transient overlays", () => {
    const overlays = sectionBetween(
      "/* High-blur glass is reserved for transient overlay surfaces. */",
      ':root[data-theme="dark"] .toast',
    );

    for (const selector of overlaySelectors) {
      expect(overlays).toContain(selector);
    }
    expect(overlays).toContain("blur(38px) saturate(1.32)");
    expect(overlays.match(/radial-gradient\(/g)).toHaveLength(2);
    expect(overlays).toContain("background-size: 4px 4px, 7px 7px, auto");
  });

  it("keeps the overlay treatment legible in the dark theme", () => {
    const darkOverlays = sectionBetween(
      ':root[data-theme="dark"] .toast',
      "@media (max-width: 980px)",
    );

    for (const selector of overlaySelectors) {
      expect(darkOverlays).toContain(selector);
    }
    expect(darkOverlays.match(/radial-gradient\(/g)).toHaveLength(2);
    expect(darkOverlays).toContain("rgba(0, 0, 0, 0.88)");
  });

  it("removes blur and noise when reduced transparency is requested", () => {
    const reduced = css.slice(
      css.indexOf("@media (prefers-reduced-transparency: reduce)"),
    );

    expect(reduced).toContain(".knowledge-hero");
    for (const selector of overlaySelectors) {
      expect(reduced).toContain(selector);
    }
    expect(reduced).toContain("background: var(--aura-backdrop-solid)");
    expect(reduced).toContain("background-image: none");
    expect(reduced).toContain("-webkit-backdrop-filter: none");
    expect(reduced).toContain("backdrop-filter: none");
  });

  it("keeps WebKit-prefixed blur declarations paired with standard ones", () => {
    for (const value of [
      "blur(20px) saturate(1.1)",
      "blur(16px) saturate(1.08)",
      "blur(18px) saturate(1.08)",
      "var(--aura-blur)",
      "blur(38px) saturate(1.32)",
    ]) {
      expect(css).toContain(`-webkit-backdrop-filter: ${value}`);
      expect(css).toContain(`backdrop-filter: ${value}`);
    }
  });
});
