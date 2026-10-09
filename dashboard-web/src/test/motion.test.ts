import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { motionDuration, motionEase } from "@/lib/motion";

const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");
const mainSource = readFileSync(
  new URL("../main.tsx", import.meta.url),
  "utf8",
);

describe("motion contract", () => {
  it("keeps JavaScript motion values aligned with CSS tokens", () => {
    expect(motionDuration).toEqual({
      instant: 0.08,
      fast: 0.12,
      control: 0.16,
      enter: 0.22,
      panel: 0.3,
    });
    expect(motionEase.out).toEqual([0.22, 0.8, 0.22, 1]);
    expect(css).toContain("--motion-instant: 80ms");
    expect(css).toContain("--motion-fast: 120ms");
    expect(css).toContain("--motion-control: 160ms");
    expect(css).toContain("--motion-enter: 220ms");
    expect(css).toContain("--motion-panel: 300ms");
    expect(css).toContain(
      "--ease-motion-out: cubic-bezier(0.22, 0.8, 0.22, 1)",
    );
  });

  it("honors the user reduced-motion preference for JavaScript motion", () => {
    expect(mainSource).toContain('<MotionConfig reducedMotion="user">');
  });
});
