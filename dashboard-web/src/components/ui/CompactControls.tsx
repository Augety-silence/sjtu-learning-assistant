import type { LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { SelectionIndicator } from "@/components/ui/SelectionIndicator";
import { cn } from "@/lib/utils";

export type CompactControlTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger";

interface CompactLabelProps extends HTMLAttributes<HTMLSpanElement> {
  children: ReactNode;
  icon?: LucideIcon;
  tone?: CompactControlTone;
}

function CompactLabel({
  children,
  className,
  icon: Icon,
  tone = "neutral",
  ...props
}: CompactLabelProps) {
  return (
    <span
      className={cn("aura-compact-label", `aura-tone-${tone}`, className)}
      {...props}
    >
      {Icon && <Icon aria-hidden="true" />}
      <span>{children}</span>
    </span>
  );
}

export function MetaTag(props: CompactLabelProps) {
  return (
    <CompactLabel {...props} className={cn("aura-meta-tag", props.className)} />
  );
}

export function StatusBadge(props: CompactLabelProps) {
  return (
    <CompactLabel
      {...props}
      className={cn("aura-status-badge", props.className)}
    />
  );
}

interface FilterChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  count?: number;
  pressed: boolean;
  /** Opt-in shared layoutId: the pressed highlight glides between chips of one group. */
  indicatorId?: string;
}

export function FilterChip({
  children,
  className,
  count,
  pressed,
  indicatorId,
  type = "button",
  ...props
}: FilterChipProps) {
  return (
    <button
      type={type}
      className={cn(
        "aura-filter-chip",
        indicatorId && "selection-host",
        indicatorId && pressed && "selection-active",
        className,
      )}
      aria-pressed={pressed}
      {...props}
    >
      {indicatorId && pressed && (
        <SelectionIndicator
          layoutId={indicatorId}
          radius={8}
          className="filter-chip-indicator"
        />
      )}
      <span>{children}</span>
      {count !== undefined && (
        <span className="aura-filter-count" aria-hidden="true">
          {count}
        </span>
      )}
    </button>
  );
}
