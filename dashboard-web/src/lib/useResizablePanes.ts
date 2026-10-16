import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

type Pane = "left" | "right";

const LEFT_MIN = 208;
const LEFT_MAX = 320;
const RIGHT_MIN = 300;
const RIGHT_MAX = 340;
const STEP = 8;
const RESIZE_KEYS = new Set(["ArrowLeft", "ArrowRight", "Home", "End"]);

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function storedWidth(key: string, fallback: number, min: number, max: number) {
  const value = Number(window.localStorage.getItem(key));
  return Number.isFinite(value) && value > 0
    ? clamp(Math.round(value), min, max)
    : fallback;
}

export function useResizablePanes() {
  const [leftWidth, setLeftWidth] = useState(() =>
    storedWidth("ai-sidebar-width", 240, LEFT_MIN, LEFT_MAX),
  );
  const [rightWidth, setRightWidth] = useState(() =>
    storedWidth("ai-activity-width", 320, RIGHT_MIN, RIGHT_MAX),
  );
  const widths = useRef({ left: leftWidth, right: rightWidth });
  const cleanupDrag = useRef<(() => void) | null>(null);

  useEffect(() => () => cleanupDrag.current?.(), []);

  const beginResize = useCallback(
    (pane: Pane, event: PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      cleanupDrag.current?.();
      const startX = event.clientX;
      const startWidth = widths.current[pane];
      const previousCursor = document.body.style.cursor;
      const previousSelection = document.body.style.userSelect;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      let frame: number | null = null;
      let pendingWidth = startWidth;

      const applyWidth = () => {
        frame = null;
        widths.current[pane] = pendingWidth;
        if (pane === "left") setLeftWidth(pendingWidth);
        else setRightWidth(pendingWidth);
      };
      const move = (moveEvent: globalThis.PointerEvent) => {
        const delta = Math.round(moveEvent.clientX - startX);
        pendingWidth =
          pane === "left"
            ? clamp(startWidth + delta, LEFT_MIN, LEFT_MAX)
            : clamp(startWidth - delta, RIGHT_MIN, RIGHT_MAX);
        if (frame === null) frame = window.requestAnimationFrame(applyWidth);
      };
      const cleanup = () => {
        if (frame !== null) window.cancelAnimationFrame(frame);
        frame = null;
        document.body.style.cursor = previousCursor;
        document.body.style.userSelect = previousSelection;
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
        window.removeEventListener("pointercancel", stop);
        window.removeEventListener("blur", stop);
        cleanupDrag.current = null;
      };
      const stop = () => {
        cleanup();
        // The last pointer move may arrive before the scheduled paint.
        applyWidth();
        window.localStorage.setItem(
          pane === "left" ? "ai-sidebar-width" : "ai-activity-width",
          String(pendingWidth),
        );
      };

      cleanupDrag.current = cleanup;
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop, { once: true });
      window.addEventListener("pointercancel", stop, { once: true });
      window.addEventListener("blur", stop);
    },
    [],
  );

  const resizeWithKeyboard = useCallback(
    (pane: Pane, event: KeyboardEvent<HTMLDivElement>) => {
      if (!RESIZE_KEYS.has(event.key)) return;
      event.preventDefault();
      const amount = event.shiftKey ? STEP * 3 : STEP;
      const direction = event.key === "ArrowRight" ? 1 : -1;
      let next =
        pane === "left"
          ? clamp(widths.current.left + direction * amount, LEFT_MIN, LEFT_MAX)
          : clamp(
              widths.current.right - direction * amount,
              RIGHT_MIN,
              RIGHT_MAX,
            );
      if (event.key === "Home") next = pane === "left" ? LEFT_MIN : RIGHT_MIN;
      if (event.key === "End") next = pane === "left" ? LEFT_MAX : RIGHT_MAX;
      widths.current[pane] = next;
      if (pane === "left") setLeftWidth(next);
      else setRightWidth(next);
      window.localStorage.setItem(
        pane === "left" ? "ai-sidebar-width" : "ai-activity-width",
        String(next),
      );
    },
    [],
  );

  const style = {
    "--ai-sidebar-width": `${leftWidth}px`,
    "--ai-activity-width": `${rightWidth}px`,
  } as CSSProperties;

  return {
    leftWidth,
    rightWidth,
    style,
    beginResize,
    resizeWithKeyboard,
  };
}
