import { Eye, EyeOff } from "lucide-react";
import { AnimatePresence, motion as m, useIsPresent } from "motion/react";
import { type ReactNode, useId } from "react";
import { Button } from "@/components/ui/Button";
import { easeOutTransition, motionDuration } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { useWidgetMotion, type WidgetMotionMode } from "./widgetMotion";
import "./recall-card.css";

export type RecallCardVariant = "expand" | "crossfade";

export interface RecallCardProps {
  question?: string | null;
  answer?: string | null;
  /** Controlled. The answer is not rendered at all while this is false. */
  revealed: boolean;
  /** Called with the requested state; the host decides whether to apply it. */
  onRevealChange: (revealed: boolean) => void;
  /** Optional provenance supplied by the host, e.g. "讲义 · 第 3 讲". */
  sourceLabel?: string | null;
  /** "expand" (recommended) grows below the question; "crossfade" swaps in place. */
  variant?: RecallCardVariant;
  motion?: WidgetMotionMode;
  /** Small heading above the question. */
  label?: string;
  className?: string;
}

/**
 * Wrapper for content that may be exiting. While AnimatePresence plays the
 * exit, the node is made inert and aria-hidden so a hidden answer is never
 * reachable by keyboard or assistive technology.
 */
function Presence({
  className,
  children,
  animate,
  variant,
  ...rest
}: {
  className: string;
  children: ReactNode;
  animate: boolean;
  variant: RecallCardVariant;
  id?: string;
  role?: "region";
  "aria-label"?: string;
}) {
  const present = useIsPresent();
  const hiddenProps = present
    ? {}
    : { inert: true, "aria-hidden": true as const, "data-exiting": "" };
  if (!animate)
    return (
      <div className={className} {...rest} {...hiddenProps}>
        {children}
      </div>
    );
  const expand = variant === "expand";
  return (
    <m.div
      className={className}
      {...rest}
      {...hiddenProps}
      initial={expand ? { opacity: 0, height: 0 } : { opacity: 0 }}
      animate={expand ? { opacity: 1, height: "auto" } : { opacity: 1 }}
      exit={expand ? { opacity: 0, height: 0 } : { opacity: 0 }}
      transition={easeOutTransition(
        expand ? motionDuration.enter : motionDuration.fast,
      )}
    >
      {children}
    </m.div>
  );
}

export function RecallCard({
  question,
  answer,
  revealed,
  onRevealChange,
  sourceLabel,
  variant = "expand",
  motion = "auto",
  label = "复习题",
  className,
}: RecallCardProps) {
  const animate = useWidgetMotion(motion);
  const answerId = useId();
  const questionText = question?.trim() || null;
  const answerText = answer?.trim() || null;
  const source = sourceLabel?.trim() || null;

  const answerNode = (
    <Presence
      key="answer"
      className="slw-recall-answer"
      animate={animate}
      variant={variant}
      id={answerId}
      role="region"
      aria-label="参考答案"
    >
      <span className="slw-recall-answer-inner">
        <span className="slw-recall-answer-label">参考答案</span>
        {answerText ? (
          <span className="slw-recall-answer-text">{answerText}</span>
        ) : (
          <span className="slw-recall-missing">
            未提供参考答案。可以回到视频或讲义自行核对。
          </span>
        )}
      </span>
    </Presence>
  );

  const placeholderNode = (
    <Presence
      key="placeholder"
      className="slw-recall-placeholder"
      animate={animate}
      variant={variant}
    >
      答案已隐藏，想好后再点“显示答案”。
    </Presence>
  );

  const slot =
    variant === "crossfade" ? (
      <div className="slw-recall-slot" data-variant="crossfade">
        <AnimatePresence initial={false}>
          {revealed ? answerNode : placeholderNode}
        </AnimatePresence>
      </div>
    ) : (
      <div className="slw-recall-slot" data-variant="expand">
        <AnimatePresence initial={false}>
          {revealed && answerNode}
        </AnimatePresence>
      </div>
    );

  return (
    <section
      className={cn("slw-recall", className)}
      data-variant={variant}
      data-motion={animate ? "light" : "static"}
      data-revealed={revealed || undefined}
      aria-label={label}
    >
      <header className="slw-recall-head">
        <span className="slw-recall-label">{label}</span>
        {source && <span className="slw-recall-source">{source}</span>}
      </header>
      {questionText ? (
        <p className="slw-recall-question">{questionText}</p>
      ) : (
        <p className="slw-recall-question slw-recall-missing">未提供题目。</p>
      )}
      {slot}
      <div className="slw-recall-actions">
        <Button
          type="button"
          size="sm"
          variant={revealed ? "ghost" : "outline"}
          aria-expanded={revealed}
          aria-controls={revealed ? answerId : undefined}
          onClick={() => onRevealChange(!revealed)}
        >
          {revealed ? (
            <EyeOff aria-hidden="true" />
          ) : (
            <Eye aria-hidden="true" />
          )}
          {revealed ? "隐藏答案" : "显示答案"}
        </Button>
      </div>
    </section>
  );
}
