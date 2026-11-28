import * as TabsPrimitive from "@radix-ui/react-tabs";
import { motion, useReducedMotion } from "motion/react";
import * as React from "react";
import { selectionIndicatorDuration } from "@/components/ui/SelectionIndicator";
import { useUIRelease } from "@/lib/uiRelease";
import { cn } from "@/lib/utils";

/** Active tab value, mirrored so triggers know where the sliding rectangle lives. */
const TabsValueContext = React.createContext<string | undefined>(undefined);
const TabsIndicatorContext = React.createContext(false);

export const Tabs = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Root>
>(({ value, defaultValue, onValueChange, ...props }, ref) => {
  const [uncontrolled, setUncontrolled] = React.useState(defaultValue);
  return (
    <TabsValueContext.Provider value={value ?? uncontrolled}>
      <TabsPrimitive.Root
        ref={ref}
        value={value}
        defaultValue={defaultValue}
        onValueChange={(next) => {
          setUncontrolled(next);
          onValueChange?.(next);
        }}
        {...props}
      />
    </TabsValueContext.Provider>
  );
});
Tabs.displayName = TabsPrimitive.Root.displayName;

export const TabsContent = TabsPrimitive.Content;

export const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List> & {
    /** Opt-in: one rectangle glides to the active trigger instead of switching backgrounds. */
    indicator?: boolean;
    /** Corner radius of the sliding rectangle (match the trigger radius). */
    indicatorRadius?: number;
  }
>(
  (
    { className, children, indicator = false, indicatorRadius = 8, ...props },
    ref,
  ) => {
    const release = useUIRelease();
    const listRef = React.useRef<HTMLDivElement | null>(null);
    const value = React.useContext(TabsValueContext);
    const reducedMotion = useReducedMotion();
    const [box, setBox] = React.useState<{
      x: number;
      y: number;
      width: number;
      height: number;
      duration: number;
    } | null>(null);
    React.useLayoutEffect(() => {
      const list = listRef.current;
      if (!indicator || !list) {
        setBox(null);
        return;
      }
      const measure = () => {
        const trigger = list.querySelector<HTMLElement>(
          '[role="tab"][data-state="active"]',
        );
        if (!trigger) {
          setBox(null);
          return;
        }
        const next = {
          x: trigger.offsetLeft,
          y: trigger.offsetTop,
          width: trigger.offsetWidth,
          height: trigger.offsetHeight,
        };
        setBox((previous) => {
          if (
            previous &&
            Object.entries(next).every(
              ([key, number]) => previous[key as keyof typeof next] === number,
            )
          )
            return previous;
          return {
            ...next,
            duration: selectionIndicatorDuration(
              previous
                ? Math.hypot(next.x - previous.x, next.y - previous.y)
                : 0,
            ),
          };
        });
      };
      measure();
      const observer =
        typeof ResizeObserver === "undefined"
          ? null
          : new ResizeObserver(measure);
      observer?.observe(list);
      for (const trigger of list.querySelectorAll('[role="tab"]'))
        observer?.observe(trigger);
      window.addEventListener("resize", measure);
      return () => {
        observer?.disconnect();
        window.removeEventListener("resize", measure);
      };
    }, [indicator, value, children]);
    return (
      <TabsIndicatorContext.Provider value={indicator}>
        <TabsPrimitive.List
          ref={(node) => {
            listRef.current = node;
            if (typeof ref === "function") ref(node);
            else if (ref) ref.current = node;
          }}
          className={cn(
            release === "2"
              ? "v2-tabs"
              : "aura-tabs-list inline-flex h-9 items-center rounded-[11px] bg-control-fill p-1 text-caption",
            indicator && release !== "2" && "tabs-sliding-group",
            className,
          )}
          {...props}
        >
          {indicator && release !== "2" && box && (
            <motion.span
              aria-hidden="true"
              data-selection-indicator
              className="selection-indicator tabs-indicator tabs-group-indicator"
              initial={false}
              animate={{
                x: box.x,
                y: box.y,
                width: box.width,
                height: box.height,
              }}
              style={{ borderRadius: indicatorRadius }}
              transition={
                reducedMotion
                  ? { duration: 0 }
                  : { type: "spring", bounce: 0.04, duration: box.duration }
              }
            />
          )}
          {children}
        </TabsPrimitive.List>
      </TabsIndicatorContext.Provider>
    );
  },
);
TabsList.displayName = TabsPrimitive.List.displayName;

export const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, children, value, ...props }, ref) => {
  const release = useUIRelease();
  const indicator = React.useContext(TabsIndicatorContext);
  const active = React.useContext(TabsValueContext) === value;
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      value={value}
      className={cn(
        release === "2"
          ? "v2-tab"
          : "aura-tabs-trigger inline-flex h-8 items-center justify-center whitespace-nowrap rounded-[8px] px-3 text-sm font-normal hover:bg-neutral-hover active:bg-surface-hover focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary focus-visible:[outline-style:solid] disabled:pointer-events-none disabled:opacity-50 data-[state=active]:bg-surface-elevated data-[state=active]:font-medium data-[state=active]:text-ink",
        indicator && "selection-host tabs-sliding-trigger",
        indicator && active && "selection-active",
        className,
      )}
      {...props}
    >
      {indicator ? (
        <span className="selection-label">{children}</span>
      ) : (
        children
      )}
    </TabsPrimitive.Trigger>
  );
});
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;
