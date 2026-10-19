import { motion, type Transition } from "motion/react";
import { useId } from "react";
import { cn } from "@/lib/utils";

/**
 * Same soft spring as the sidebar rectangle. Reduced-motion users get an
 * instant jump through the root `MotionConfig reducedMotion="user"`.
 */
export const selectionIndicatorTransition: Transition = {
  type: "spring",
  bounce: 0.04,
  duration: 0.15,
};

/** A layoutId that is unique per component instance, so two groups never trade rectangles. */
export function useSelectionIndicatorId(prefix: string) {
  return `${prefix}-${useId()}`;
}

/**
 * One shared highlight rectangle that glides between the options of a group
 * (Motion shared layout via `layoutId`). Render it only inside the selected
 * option; the host must carry `.selection-host` (and `.selection-active` when
 * selected) so the static selected background is handed over to this element.
 *
 * `radius` is passed as an inline style so Motion can scale-correct the corners
 * while the rectangle stretches between options of different widths.
 */
export function SelectionIndicator({
  layoutId,
  radius,
  className,
}: {
  layoutId: string;
  radius: number;
  className?: string;
}) {
  return (
    <motion.span
      layoutId={layoutId}
      aria-hidden="true"
      data-selection-indicator
      className={cn("selection-indicator", className)}
      style={{ borderRadius: radius }}
      transition={selectionIndicatorTransition}
    />
  );
}
