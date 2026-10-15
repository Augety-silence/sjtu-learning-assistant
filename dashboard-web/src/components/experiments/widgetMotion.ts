import { useReducedMotion, useReducedMotionConfig } from "motion/react";

/**
 * Motion preference for experimental widgets.
 * - "auto": light motion, automatically downgraded when the user (OS) or the
 *   surrounding MotionConfig asks for reduced motion.
 * - "static": never animate; used for side-by-side comparison and for hosts
 *   that want a fully static presentation.
 */
export type WidgetMotionMode = "auto" | "static";

export function useWidgetMotion(mode: WidgetMotionMode = "auto"): boolean {
  const systemReduced = useReducedMotion();
  const configReduced = useReducedMotionConfig();
  return mode === "auto" && !systemReduced && !configReduced;
}

export function isFiniteNonNegative(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

/** Format milliseconds as mm:ss, or h:mm:ss for media longer than an hour. */
export function formatClockMs(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}
