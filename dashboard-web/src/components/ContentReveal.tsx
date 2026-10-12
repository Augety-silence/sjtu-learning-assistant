import { useReducedMotion } from "motion/react";
import { type CSSProperties, type HTMLAttributes, useState } from "react";
import { motionDuration } from "@/lib/motion";

interface ContentRevealProps extends HTMLAttributes<HTMLElement> {
  as?: "div" | "section" | "main";
  ready?: boolean;
  afterLoad?: boolean;
  variant?: "page" | "detail";
  sequence?: "children" | "manual";
}

/** Keep the original root and children mounted; only reveal a ready body. */
export function ContentReveal({
  as: Element = "div",
  ready = true,
  afterLoad = false,
  variant = "page",
  sequence = "children",
  style,
  children,
  ...props
}: ContentRevealProps) {
  const reducedMotion = useReducedMotion();
  const [hasWaited, setHasWaited] = useState(!ready);
  const [hasRevealed, setHasRevealed] = useState(ready);
  if (!ready && !hasWaited) setHasWaited(true);
  if (ready && !hasRevealed) setHasRevealed(true);
  const duration =
    variant === "detail"
      ? motionDuration.detailReveal
      : afterLoad || hasWaited
        ? motionDuration.loadedReveal
        : motionDuration.contentReveal;
  return (
    <Element
      {...props}
      data-reveal-variant={variant}
      data-page-reveal={
        variant === "page"
          ? reducedMotion
            ? "reduced"
            : hasRevealed
              ? "ready"
              : "loading"
          : undefined
      }
      data-page-reveal-sequence={variant === "page" ? sequence : undefined}
      data-content-reveal={
        reducedMotion ? "reduced" : ready ? "ready" : "loading"
      }
      style={
        {
          ...style,
          "--content-reveal-duration": `${duration * 1000}ms`,
        } as CSSProperties
      }
    >
      {children}
    </Element>
  );
}
