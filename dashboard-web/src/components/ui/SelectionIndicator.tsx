import { motion, type Transition } from "motion/react";
import { useEffect, useId, useRef } from "react";
import { cn } from "@/lib/utils";

const INDICATOR_BOUNCE = 0.04;
const MIN_INDICATOR_DURATION = 0.12;
const MAX_INDICATOR_DURATION = 0.28;
const INDICATOR_PIXELS_PER_SECOND = 2400;
const DEFAULT_INDICATOR_DURATION = 0.15;
const PREPARED_DURATION_TTL_MS = 1_000;

/**
 * Keep adjacent moves crisp while giving long travel enough time to remain
 * legible. The cap keeps navigation responsive even across the full sidebar.
 */
export function selectionIndicatorDuration(distance: number) {
  const safeDistance = Number.isFinite(distance) ? Math.max(0, distance) : 0;
  return Math.min(
    MAX_INDICATOR_DURATION,
    MIN_INDICATOR_DURATION + safeDistance / INDICATOR_PIXELS_PER_SECOND,
  );
}

interface IndicatorCenter {
  x: number;
  y: number;
}

interface PreparedIndicatorTransition {
  duration: number;
  preparedAt: number;
}

const preparedIndicatorTransitions = new Map<
  string,
  PreparedIndicatorTransition
>();

function indicatorCenter(element: HTMLElement): IndicatorCenter {
  const rect = element.getBoundingClientRect();
  return {
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
  };
}

export function useDistanceAwareIndicatorTransition(layoutId: string) {
  const elementRef = useRef<HTMLSpanElement | null>(null);
  const prepared = preparedIndicatorTransitions.get(layoutId);
  const duration =
    prepared && Date.now() - prepared.preparedAt <= PREPARED_DURATION_TTL_MS
      ? prepared.duration
      : DEFAULT_INDICATOR_DURATION;

  useEffect(() => {
    const prepareFromTarget = (target: EventTarget | null) => {
      if (!(target instanceof Element)) return;
      const targetHost = target.closest<HTMLElement>(
        "[data-selection-layout-id]",
      );
      if (targetHost?.dataset.selectionLayoutId !== layoutId) return;

      const indicator = elementRef.current;
      const currentHost = indicator?.closest<HTMLElement>(
        "[data-selection-layout-id]",
      );
      if (!indicator || targetHost === currentHost) return;

      const from = indicatorCenter(indicator);
      const to = indicatorCenter(targetHost);
      preparedIndicatorTransitions.set(layoutId, {
        duration: selectionIndicatorDuration(
          Math.hypot(to.x - from.x, to.y - from.y),
        ),
        preparedAt: Date.now(),
      });
    };
    const prepareFromEvent = (event: Event) => prepareFromTarget(event.target);

    // Capture before React/Radix updates selection so the entering Motion
    // element receives the distance-aware duration on its first render.
    document.addEventListener("click", prepareFromEvent, true);
    document.addEventListener("focusin", prepareFromEvent, true);
    return () => {
      document.removeEventListener("click", prepareFromEvent, true);
      document.removeEventListener("focusin", prepareFromEvent, true);
    };
  }, [layoutId]);

  const transition: Transition = {
    type: "spring",
    bounce: INDICATOR_BOUNCE,
    duration,
  };

  return { ref: elementRef, transition };
}

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
  const { ref, transition } = useDistanceAwareIndicatorTransition(layoutId);
  return (
    <motion.span
      ref={ref}
      layoutId={layoutId}
      aria-hidden="true"
      data-selection-indicator
      className={cn("selection-indicator", className)}
      style={{ borderRadius: radius }}
      transition={transition}
    />
  );
}
