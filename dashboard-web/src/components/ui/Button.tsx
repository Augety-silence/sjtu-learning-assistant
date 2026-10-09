import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "aura-button inline-flex min-h-9 items-center justify-center gap-2 whitespace-nowrap rounded-[10px] px-4 text-sm font-normal transition-[color,background-color,border-color,box-shadow,transform,opacity] duration-[var(--motion-control)] ease-out active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 disabled:active:scale-100",
  {
    variants: {
      variant: {
        default:
          "aura-button-primary bg-primary text-inverse hover:bg-primary-hover active:bg-primary-pressed",
        outline:
          "aura-button-outline border border-component bg-surface-elevated text-ink hover:bg-surface-hover",
        ghost:
          "aura-button-ghost bg-transparent text-caption hover:bg-neutral-hover hover:text-ink",
        link: "min-h-9 px-2 text-primary hover:text-primary-hover",
      },
      size: {
        default: "min-h-9",
        sm: "min-h-9 px-3 text-xs",
        icon: "h-9 w-9 px-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
  loadingLabel?: string;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      children,
      className,
      variant,
      size,
      asChild = false,
      disabled,
      loading = false,
      loadingLabel,
      ...props
    },
    ref,
  ) => {
    const Component = asChild ? Slot : "button";
    return (
      <Component
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        aria-busy={loading || undefined}
        disabled={!asChild ? disabled || loading : undefined}
        {...props}
      >
        {loading && <span className="button-spinner" aria-hidden="true" />}
        {loading && loadingLabel ? loadingLabel : children}
      </Component>
    );
  },
);
Button.displayName = "Button";
