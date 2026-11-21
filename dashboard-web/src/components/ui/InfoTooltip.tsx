import { Info } from "lucide-react";
import {
  type CSSProperties,
  type ReactNode,
  useId,
  useRef,
  useState,
} from "react";
import { Button } from "@/components/ui/Button";
import "./info-tooltip.css";

export function InfoTooltip({
  label,
  children,
  className = "",
  align = "start",
}: {
  label: string;
  children: ReactNode;
  className?: string;
  align?: "start" | "end";
}) {
  const id = useId();
  const host = useRef<HTMLSpanElement>(null);
  const [dismissed, setDismissed] = useState(false);
  const [placement, setPlacement] = useState({ shift: 0, above: false });
  const reveal = () => {
    setDismissed(false);
    const bounds = host.current?.getBoundingClientRect();
    if (!bounds) return;
    const bubble = host.current?.querySelector<HTMLElement>('[role="tooltip"]');
    const width = Math.min(320, window.innerWidth * 0.7);
    const left = align === "end" ? bounds.right - width : bounds.left;
    const clamped = Math.max(
      12,
      Math.min(left, window.innerWidth - width - 12),
    );
    const height = bubble?.getBoundingClientRect().height ?? 0;
    setPlacement({
      shift: clamped - left,
      above:
        height > 0 &&
        bounds.bottom + height > window.innerHeight - 12 &&
        bounds.top > height + 12,
    });
  };
  return (
    <span
      ref={host}
      className={`info-tooltip ${className}`}
      data-align={align}
      data-dismissed={dismissed}
      data-side={placement.above ? "top" : "bottom"}
      style={
        { "--info-tooltip-shift": `${placement.shift}px` } as CSSProperties
      }
      onMouseEnter={reveal}
      onFocus={reveal}
      onKeyDown={(event) => {
        if (event.key === "Escape") setDismissed(true);
      }}
    >
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={label}
        aria-describedby={id}
      >
        <Info aria-hidden="true" size={16} strokeWidth={1.5} />
      </Button>
      <span id={id} role="tooltip">
        {children}
      </span>
    </span>
  );
}
