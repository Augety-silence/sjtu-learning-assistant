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

  it("keeps AI Chat conversation width and responsive drawers intentional", () => {
    expect(css).toContain("max-width: min(76%, 620px)");
    expect(css).toContain("max-width: 820px");
    expect(css).toContain("@media (min-width: 1050px) and (max-width: 1349px)");
    expect(css).toContain("@media (min-width: 850px) and (max-width: 1049px)");
    expect(css).toContain(
      "grid-template-columns: clamp(208px, 18vw, 236px) 6px minmax(500px, 1fr)",
    );
    expect(css).not.toContain(
      "grid-template-columns: clamp(220px, 20vw, 260px) 6px minmax(0, 1fr) 300px",
    );
    expect(css).not.toContain(
      "grid-template-columns: var(--ai-sidebar-width) 6px minmax(0, 1fr)",
    );
    expect(css).toContain("width: min(320px, calc(100vw - 500px))");
    expect(css).toContain(".ai-trace-step");
    expect(css).toMatch(
      /\.ai-trace-node \{[^}]*position: static[^}]*min-width: 24px[^}]*border: 0/,
    );
    expect(css).toMatch(
      /\.ai-trace-step > summary \{[^}]*width: 100%[^}]*min-width: 0/,
    );
    expect(css).toMatch(
      /\.ai-trace-summary-copy strong \{[^}]*overflow-wrap: normal[^}]*word-break: normal[^}]*white-space: normal/,
    );
    expect(css).not.toContain(".ai-activity-backdrop");
    expect(css).toContain("--ai-floating-activity-width: min(340px, 42vw)");
  });

  it("keeps AI message and thinking rows horizontal without breaking normal CJK", () => {
    expect(css).not.toMatch(/\.ai-message > header \{[^}]*display: contents/);
    expect(css).toMatch(
      /\.ai-message-avatar \{[^}]*min-width: 34px[^}]*grid-column: 1[^}]*grid-row: 1/,
    );
    expect(css).toMatch(
      /\.ai-markdown \{[^}]*overflow-wrap: normal[^}]*word-break: normal/,
    );
    expect(css).toMatch(
      /\.ai-thinking-copy \{[^}]*display: flex[^}]*flex-wrap: wrap/,
    );
    expect(css).toContain(".ai-chat-messages .ai-thinking > span:first-child");
  });

  it("keeps the composer compact and caps its internal scroller", () => {
    expect(css).toMatch(
      /\.ai-composer textarea \{[^}]*height: 42px[^}]*min-height: 42px[^}]*max-height: min\(160px, 32vh\)[^}]*resize: none/,
    );
    expect(css).toMatch(
      /\.ai-conversation\.is-empty \.ai-composer textarea \{[^}]*min-height: 42px/,
    );
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

  it("keeps contextual illustrations contained and non-interactive", () => {
    expect(css).toMatch(
      /\.state-illustration img \{[^}]*max-width:[^}]*max-height:[^}]*object-fit: contain/,
    );
    expect(css).toMatch(
      /\.ai-chat-empty-illustration \{[^}]*object-fit: contain[^}]*pointer-events: none/,
    );
    expect(css).toContain(".settings-first-config-illustration");
    expect(css).toContain(".submission-success-illustration");
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

  it("uses container-based video table breakpoints without changing AI panel width", () => {
    expect(css).toContain("container: video-list / inline-size");
    expect(css).toContain("@container video-list (max-width: 859px)");
    expect(css).toContain("@container video-list (max-width: 679px)");
    expect(css).toMatch(
      /\.video-table th,[\s\S]*?\.video-table td {[^}]*white-space: normal/,
    );
    expect(css).toContain("width: min(336px, calc(100% - 16px))");
  });

  it("allows critical course and recording titles to wrap without horizontal overflow", () => {
    expect(css).toMatch(
      /\.video-course-trigger > span,[\s\S]*?\.video-course-readonly \{[^}]*overflow-wrap: anywhere[^}]*white-space: normal/,
    );
    expect(css).toMatch(
      /\.video-recording-copy > strong \{[^}]*overflow-wrap: anywhere[^}]*white-space: normal/,
    );
    expect(css).toMatch(
      /\.video-now-playing strong \{[^}]*overflow-wrap: anywhere[^}]*white-space: normal/,
    );
  });

  it("keeps recording more actions visible on touch and keyboard-only layouts", () => {
    expect(css).toMatch(/\.video-more \{[^}]*opacity: 1/);
    expect(css).toMatch(
      /@media \(hover: hover\) \{[\s\S]*?\.video-more \{[^}]*opacity: 0[^}]*\}[\s\S]*?\.video-recording-list > li:focus-within \.video-more \{ opacity: 1; \}/,
    );
    expect(css).not.toContain("@media (hover: none)");
  });
});
