import {
  type CSSProperties,
  type ReactNode,
  type RefObject,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

/** 菜单使用独立浮层，避免挤占列表或被输入区裁剪。 */
export function AIFloatingMenu({
  anchorRef,
  children,
  onClose,
  className = "",
  width = 260,
  placement = "auto",
}: {
  anchorRef: RefObject<HTMLElement | null>;
  children: ReactNode;
  onClose: () => void;
  className?: string;
  width?: number;
  placement?: "auto" | "above";
}) {
  const root = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const [style, setStyle] = useState<CSSProperties>({ visibility: "hidden" });
  useLayoutEffect(() => {
    const position = () => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const r = anchor.getBoundingClientRect();
      const w = Math.min(width, window.innerWidth - 24);
      const availableBelow = window.innerHeight - r.bottom - 20;
      const above =
        placement === "above" ||
        (availableBelow < 220 && r.top > availableBelow);
      setStyle({
        width: w,
        left: Math.max(12, Math.min(r.left, window.innerWidth - w - 12)),
        ...(above
          ? { bottom: window.innerHeight - r.top + 6 }
          : { top: r.bottom + 6 }),
        maxHeight: Math.max(
          80,
          Math.min(400, above ? r.top - 20 : availableBelow),
        ),
      });
    };
    position();
    const dismiss = (e: Event) => {
      const target = e.target as Node;
      if (
        !root.current?.contains(target) &&
        !anchorRef.current?.contains(target)
      )
        close.current();
    };
    const keyboard = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.isComposing) {
        e.preventDefault();
        e.stopPropagation();
        close.current();
        anchorRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", keyboard, true);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", keyboard, true);
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
    };
  }, [anchorRef, width, placement]);
  return createPortal(
    <div
      ref={root}
      data-ai-floating-menu
      className={`ui2-floating-menu ${className}`}
      style={style}
    >
      {children}
    </div>,
    document.body,
  );
}
