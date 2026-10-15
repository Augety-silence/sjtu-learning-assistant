import { RefreshCw, X } from "lucide-react";
import { AnimatePresence, motion as m } from "motion/react";
import { type FocusEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { easeOutTransition, motionDuration } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { useWidgetMotion, type WidgetMotionMode } from "./widgetMotion";
import "./material-update-notice.css";

export type MaterialRevision = string | number | null | undefined;

export interface MaterialUpdateNoticeProps {
  /**
   * Opaque revision of the learning materials (e.g. `learningRevision`).
   * The first non-empty value is taken as the baseline and never notifies;
   * only a later, different non-empty value shows the notice.
   * `null` / `undefined` / `""` mean "unknown" and are ignored.
   */
  revision: MaterialRevision;
  /** Optional extra line supplied by the host. Shown verbatim. */
  message?: string | null;
  /** Optional. Only called when the user presses "查看". */
  onView?: () => void;
  /**
   * Hide automatically after this many ms; paused while hovered or focused.
   * `null` or `0` keeps the notice until the user closes it. Default 6000.
   */
  autoHideMs?: number | null;
  motion?: WidgetMotionMode;
  className?: string;
}

function normalizeRevision(revision: MaterialRevision): string | null {
  if (revision === null || revision === undefined) return null;
  if (typeof revision === "number" && !Number.isFinite(revision)) return null;
  const text = String(revision);
  return text === "" ? null : text;
}

/**
 * Returns a counter that increases only when the revision really changes
 * from a known value to a different known value. Re-renders, polling with the
 * same revision, StrictMode double effects and temporary `null`s do not bump it.
 */
export function useRevisionChange(revision: MaterialRevision): number {
  const baselineRef = useRef<string | null>(null);
  const [changeCount, setChangeCount] = useState(0);
  const normalized = normalizeRevision(revision);
  useEffect(() => {
    if (normalized === null) return;
    if (baselineRef.current === null) {
      baselineRef.current = normalized;
      return;
    }
    if (baselineRef.current === normalized) return;
    baselineRef.current = normalized;
    setChangeCount((count) => count + 1);
  }, [normalized]);
  return changeCount;
}

export function MaterialUpdateNotice({
  revision,
  message,
  onView,
  autoHideMs = 6000,
  motion = "auto",
  className,
}: MaterialUpdateNoticeProps) {
  const animate = useWidgetMotion(motion);
  const changeCount = useRevisionChange(revision);
  // Change count at which the user (or the timer) last dismissed the notice.
  const [dismissedAt, setDismissedAt] = useState(0);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const paused = hovered || focused;
  // Derived, so a further change while visible keeps the same element
  // mounted (no exit/enter replay) and only restarts the auto-hide timer.
  const visible = changeCount > dismissedAt;
  const extra = message?.trim() || null;

  useEffect(() => {
    if (!visible || paused || !autoHideMs || autoHideMs <= 0) return;
    const timer = window.setTimeout(
      () => setDismissedAt(changeCount),
      autoHideMs,
    );
    return () => window.clearTimeout(timer);
  }, [visible, paused, autoHideMs, changeCount]);

  useEffect(() => {
    if (visible) return;
    setHovered(false);
    setFocused(false);
  }, [visible]);

  const dismiss = () => setDismissedAt(changeCount);

  const body = (
    <>
      <RefreshCw aria-hidden="true" className="slw-update-icon" />
      <span className="slw-update-text">
        <span className="slw-update-title">材料有更新</span>
        {extra && <span className="slw-update-message">{extra}</span>}
      </span>
      {onView && (
        <Button
          type="button"
          size="sm"
          variant="link"
          className="slw-update-view"
          onClick={() => {
            onView();
            dismiss();
          }}
        >
          查看
        </Button>
      )}
      <button
        type="button"
        className="slw-update-close"
        aria-label="关闭更新提示"
        onClick={dismiss}
      >
        <X aria-hidden="true" />
      </button>
    </>
  );

  const noticeProps = {
    className: "slw-update-notice",
    onPointerEnter: () => setHovered(true),
    onPointerLeave: () => setHovered(false),
    onFocus: () => setFocused(true),
    onBlur: (event: FocusEvent<HTMLSpanElement>) => {
      const next = event.relatedTarget as Node | null;
      if (!next || !event.currentTarget.contains(next)) setFocused(false);
    },
  };

  // The live region stays mounted so screen readers announce insertions.
  return (
    <span
      className={cn("slw-update", className)}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-motion={animate ? "light" : "static"}
      data-visible={visible || undefined}
    >
      {animate ? (
        <AnimatePresence initial={false}>
          {visible && (
            <m.span
              key="notice"
              {...noticeProps}
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={easeOutTransition(motionDuration.popoverEnter)}
            >
              {body}
            </m.span>
          )}
        </AnimatePresence>
      ) : (
        visible && <span {...noticeProps}>{body}</span>
      )}
    </span>
  );
}
