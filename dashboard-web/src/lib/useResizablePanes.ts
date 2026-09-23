import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";
import { useCallback, useEffect, useState } from "react";

type Pane = "left" | "right";

const LEFT_MIN = 208;
const LEFT_MAX = 320;
const RIGHT_MIN = 260;
const RIGHT_MAX = 420;
const STEP = 8;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function useResizablePanes() {
  const [leftWidth, setLeftWidth] = useState(240);
  const [rightWidth, setRightWidth] = useState(300);

  useEffect(() => {
    const savedLeft = Number(window.localStorage.getItem("ai-sidebar-width"));
    const savedRight = Number(window.localStorage.getItem("ai-activity-width"));
    if (Number.isFinite(savedLeft) && savedLeft > 0) {
      setLeftWidth(clamp(savedLeft, LEFT_MIN, LEFT_MAX));
    }
    if (Number.isFinite(savedRight) && savedRight > 0) {
      setRightWidth(clamp(savedRight, RIGHT_MIN, RIGHT_MAX));
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem("ai-sidebar-width", String(leftWidth));
  }, [leftWidth]);

  useEffect(() => {
    window.localStorage.setItem("ai-activity-width", String(rightWidth));
  }, [rightWidth]);

  const beginResize = useCallback(
    (pane: Pane, event: PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = pane === "left" ? leftWidth : rightWidth;
      const previousCursor = document.body.style.cursor;
      const previousSelection = document.body.style.userSelect;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";

      const move = (moveEvent: globalThis.PointerEvent) => {
        const delta = moveEvent.clientX - startX;
        if (pane === "left") {
          setLeftWidth(clamp(startWidth + delta, LEFT_MIN, LEFT_MAX));
        } else {
          setRightWidth(clamp(startWidth - delta, RIGHT_MIN, RIGHT_MAX));
        }
      };
      const stop = () => {
        document.body.style.cursor = previousCursor;
        document.body.style.userSelect = previousSelection;
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
        window.removeEventListener("pointercancel", stop);
      };

      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop, { once: true });
      window.addEventListener("pointercancel", stop, { once: true });
    },
    [leftWidth, rightWidth],
  );

  const resizeWithKeyboard = useCallback(
    (pane: Pane, event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const amount = event.shiftKey ? STEP * 3 : STEP;
      const direction = event.key === "ArrowRight" ? 1 : -1;
      if (pane === "left") {
        setLeftWidth((width) =>
          clamp(width + direction * amount, LEFT_MIN, LEFT_MAX),
        );
      } else {
        setRightWidth((width) =>
          clamp(width - direction * amount, RIGHT_MIN, RIGHT_MAX),
        );
      }
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
