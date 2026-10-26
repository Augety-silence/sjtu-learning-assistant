import * as React from "react";
import { cn } from "@/lib/utils";

export const Table = React.forwardRef<
  HTMLTableElement,
  React.HTMLAttributes<HTMLTableElement>
>(({ className, ...props }, ref) => (
  // 横向滚动区需可聚焦：WKWebView 不会自动让滚动容器获得焦点，只读宽表否则无法用方向键滚动
  // （WCAG 2.1.1 / axe scrollable-region-focusable）。
  <div
    className="w-full overflow-x-auto focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary focus-visible:[outline-style:solid]"
    role={props["aria-label"] ? "region" : undefined}
    aria-label={
      props["aria-label"] ? `${props["aria-label"]}，可横向滚动` : undefined
    }
    tabIndex={0}
  >
    <table
      ref={ref}
      className={cn("w-full table-fixed border-collapse text-sm", className)}
      {...props}
    />
  </div>
));
Table.displayName = "Table";
export const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>((props, ref) => <thead ref={ref} {...props} />);
TableHeader.displayName = "TableHeader";
export const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>((props, ref) => <tbody ref={ref} {...props} />);
TableBody.displayName = "TableBody";
export const TableRow = React.forwardRef<
  HTMLTableRowElement,
  React.HTMLAttributes<HTMLTableRowElement>
>(({ className, ...props }, ref) => (
  <tr
    ref={ref}
    className={cn(
      "interactive-table-row h-14 border-b border-neutral-divider last:border-0",
      className,
    )}
    {...props}
  />
));
TableRow.displayName = "TableRow";
export const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <th
    ref={ref}
    className={cn(
      "h-10 truncate px-4 text-left text-xs font-medium text-caption",
      className,
    )}
    {...props}
  />
));
TableHead.displayName = "TableHead";
export const TableCell = React.forwardRef<
  HTMLTableCellElement,
  React.TdHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <td
    ref={ref}
    className={cn("truncate px-4 font-normal text-ink", className)}
    {...props}
  />
));
TableCell.displayName = "TableCell";
