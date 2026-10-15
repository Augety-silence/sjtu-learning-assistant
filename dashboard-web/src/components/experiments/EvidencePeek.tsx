import { ChevronDown, Play, Quote } from "lucide-react";
import { AnimatePresence, motion as m } from "motion/react";
import {
  type FocusEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { Button } from "@/components/ui/Button";
import { easeOutTransition, motionDuration } from "@/lib/motion";
import { cn } from "@/lib/utils";
import {
  formatClockMs,
  isFiniteNonNegative,
  useWidgetMotion,
  type WidgetMotionMode,
} from "./widgetMotion";
import "./evidence-peek.css";

export type EvidencePeekVariant = "inline" | "popover";

export interface EvidencePeekProps {
  quote?: string | null;
  sourceLabel?: string | null;
  startMs?: number | null;
  endMs?: number | null;
  /** Optional. Only called when the user presses the jump button. */
  onSeekMs?: (ms: number) => void;
  /** "inline" (recommended) expands in flow; "popover" floats a light card. */
  variant?: EvidencePeekVariant;
  motion?: WidgetMotionMode;
  /** Uncontrolled initial state; mainly for previews. */
  defaultOpen?: boolean;
  className?: string;
}

export interface EvidenceTiming {
  startMs: number | null;
  endMs: number | null;
  label: string;
}

/** Validate timing without inventing values. Exported for tests and hosts. */
export function resolveEvidenceTiming(
  startMs?: number | null,
  endMs?: number | null,
): EvidenceTiming {
  if (!isFiniteNonNegative(startMs))
    return { startMs: null, endMs: null, label: "时间未知" };
  if (isFiniteNonNegative(endMs) && endMs > startMs)
    return {
      startMs,
      endMs,
      label: `${formatClockMs(startMs)}–${formatClockMs(endMs)}`,
    };
  return { startMs, endMs: null, label: formatClockMs(startMs) };
}

export function EvidencePeek({
  quote,
  sourceLabel,
  startMs,
  endMs,
  onSeekMs,
  variant = "inline",
  motion = "auto",
  defaultOpen = false,
  className,
}: EvidencePeekProps) {
  const animate = useWidgetMotion(motion);
  const [open, setOpen] = useState(defaultOpen);
  const rootRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const timing = resolveEvidenceTiming(startMs, endMs);
  const source = sourceLabel?.trim() || null;
  const text = quote?.trim() || null;
  const canSeek = Boolean(onSeekMs) && timing.startMs !== null;

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  // Popover only: dismiss on outside pointer without stealing focus.
  useEffect(() => {
    if (!open || variant !== "popover") return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, variant, close]);

  // Popover only: close when focus leaves the widget; focus is not moved.
  const onBlur = (event: FocusEvent<HTMLSpanElement>) => {
    if (variant !== "popover" || !open) return;
    const next = event.relatedTarget as Node | null;
    if (next && !rootRef.current?.contains(next)) close(false);
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape" && open) {
      event.stopPropagation();
      close(true);
    }
  };

  const summary = [source ?? "来源未标注", timing.label].join(" · ");
  const panelBody = (
    <>
      {text ? (
        <span className="slw-evidence-quote">{text}</span>
      ) : (
        <span className="slw-evidence-missing">未提供字幕引文。</span>
      )}
      <span className="slw-evidence-meta">
        <span>{source ?? "来源未标注"}</span>
        <span>{timing.label}</span>
      </span>
      {onSeekMs &&
        (canSeek ? (
          <Button
            size="sm"
            variant="outline"
            className="slw-evidence-seek"
            onClick={() => onSeekMs(timing.startMs as number)}
          >
            <Play aria-hidden="true" />
            跳到 {formatClockMs(timing.startMs as number)}
          </Button>
        ) : (
          <span className="slw-evidence-missing">
            缺少有效时间，无法跳转视频。
          </span>
        ))}
    </>
  );

  const panelClass = cn(
    "slw-evidence-panel",
    variant === "popover" && "slw-evidence-panel-popover",
  );
  const panelProps = {
    id: panelId,
    role: "region" as const,
    "aria-label": `字幕证据：${summary}`,
  };

  return (
    <span
      ref={rootRef}
      className={cn("slw-evidence", className)}
      data-variant={variant}
      data-motion={animate ? "light" : "static"}
      data-open={open || undefined}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
    >
      <button
        ref={triggerRef}
        type="button"
        className="slw-evidence-trigger"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        <Quote aria-hidden="true" />
        <span className="slw-evidence-summary">{summary}</span>
        <ChevronDown aria-hidden="true" className="slw-evidence-chevron" />
      </button>
      {animate ? (
        <AnimatePresence initial={false}>
          {open && (
            <m.span
              key="panel"
              {...panelProps}
              className={panelClass}
              initial={
                variant === "popover"
                  ? { opacity: 0, y: -4 }
                  : { opacity: 0, height: 0 }
              }
              animate={
                variant === "popover"
                  ? { opacity: 1, y: 0 }
                  : { opacity: 1, height: "auto" }
              }
              exit={
                variant === "popover"
                  ? { opacity: 0, y: -4 }
                  : { opacity: 0, height: 0 }
              }
              transition={easeOutTransition(
                variant === "popover"
                  ? motionDuration.popoverEnter
                  : motionDuration.enter,
              )}
            >
              <span className="slw-evidence-panel-inner">{panelBody}</span>
            </m.span>
          )}
        </AnimatePresence>
      ) : (
        open && (
          <span {...panelProps} className={panelClass}>
            <span className="slw-evidence-panel-inner">{panelBody}</span>
          </span>
        )
      )}
    </span>
  );
}
