export const motionDuration = {
  instant: 0.08,
  fast: 0.12,
  control: 0.16,
  enter: 0.22,
  panel: 0.3,
} as const;

export const motionEase = {
  out: [0.22, 0.8, 0.22, 1] as const,
  in: [0.4, 0, 1, 1] as const,
} as const;

export function easeOutTransition(duration: number) {
  return { duration, ease: motionEase.out };
}
