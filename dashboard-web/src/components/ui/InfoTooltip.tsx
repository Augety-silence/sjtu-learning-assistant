import { Info } from "lucide-react";
import {
  type ReactNode,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
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
  const bubble = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [placement, setPlacement] = useState({ left: 12, top: 12 });
  const cancelClose = () => {
    if (timer.current) clearTimeout(timer.current);
  };
  const closeLater = () => {
    cancelClose();
    timer.current = setTimeout(() => setOpen(false), 160);
  };
  const reveal = () => {
    cancelClose();
    setDismissed(false);
    setOpen(true);
  };
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const bounds = host.current?.getBoundingClientRect();
      const size = bubble.current?.getBoundingClientRect();
      if (!bounds || !size) return;
      const left = align === "end" ? bounds.right - size.width : bounds.left;
      const top =
        bounds.bottom + 6 + size.height > window.innerHeight - 12
          ? bounds.top - size.height - 6
          : bounds.bottom + 6;
      setPlacement({
        left: Math.max(12, Math.min(left, window.innerWidth - size.width - 12)),
        top: Math.max(12, Math.min(top, window.innerHeight - size.height - 12)),
      });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, align]);
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setDismissed(true);
        setOpen(false);
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [open]);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  return (
    <span
      ref={host}
      className={`info-tooltip ${className}`}
      data-align={align}
      data-dismissed={dismissed}
      onMouseEnter={reveal}
      onMouseLeave={closeLater}
      onFocus={reveal}
      onBlur={closeLater}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          setDismissed(true);
          setOpen(false);
        }
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
      {createPortal(
        <span
          ref={bubble}
          id={id}
          role="tooltip"
          className="info-tooltip-bubble"
          hidden={!open}
          style={placement}
          onMouseEnter={cancelClose}
          onMouseLeave={closeLater}
        >
          {children}
        </span>,
        document.body,
      )}
    </span>
  );
}
