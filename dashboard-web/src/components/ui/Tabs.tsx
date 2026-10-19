import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as React from "react";
import { SelectionIndicator } from "@/components/ui/SelectionIndicator";
import { cn } from "@/lib/utils";

/** Active tab value, mirrored so triggers know where the sliding rectangle lives. */
const TabsValueContext = React.createContext<string | undefined>(undefined);
const TabsIndicatorContext = React.createContext<{
  layoutId: string;
  radius: number;
} | null>(null);

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
>(({ className, indicator = false, indicatorRadius = 8, ...props }, ref) => {
  const id = React.useId();
  const context = React.useMemo(
    () =>
      indicator
        ? { layoutId: `tabs-indicator-${id}`, radius: indicatorRadius }
        : null,
    [indicator, indicatorRadius, id],
  );
  return (
    <TabsIndicatorContext.Provider value={context}>
      <TabsPrimitive.List
        ref={ref}
        className={cn(
          "aura-tabs-list inline-flex h-9 items-center rounded-[11px] bg-control-fill p-1 text-caption",
          className,
        )}
        {...props}
      />
    </TabsIndicatorContext.Provider>
  );
});
TabsList.displayName = TabsPrimitive.List.displayName;

export const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, children, value, ...props }, ref) => {
  const indicator = React.useContext(TabsIndicatorContext);
  const active = React.useContext(TabsValueContext) === value;
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      value={value}
      className={cn(
        "aura-tabs-trigger inline-flex h-8 items-center justify-center whitespace-nowrap rounded-[8px] px-3 text-sm font-normal hover:bg-neutral-hover active:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:pointer-events-none disabled:opacity-50 data-[state=active]:bg-surface-elevated data-[state=active]:font-medium data-[state=active]:text-ink",
        indicator && "selection-host",
        indicator && active && "selection-active",
        className,
      )}
      {...props}
    >
      {indicator && active && (
        <SelectionIndicator
          layoutId={indicator.layoutId}
          radius={indicator.radius}
          className="tabs-indicator"
        />
      )}
      {indicator ? (
        <span className="selection-label">{children}</span>
      ) : (
        children
      )}
    </TabsPrimitive.Trigger>
  );
});
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;
