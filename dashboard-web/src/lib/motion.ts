export const motionDuration = {
  instant: 0.08,
  fast: 0.12,
  control: 0.14,
  enter: 0.18,
  panel: 0.22,
} as const;

export const motionEase = {
  out: [0.16, 1, 0.3, 1] as const,
  in: [0.4, 0, 1, 1] as const,
} as const;

export function easeOutTransition(duration: number) {
  return { duration, ease: motionEase.out };
}
