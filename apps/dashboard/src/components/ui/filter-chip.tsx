import * as React from "react";
import { cn } from "@/lib/utils";

export interface FilterChipProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean;
  count?: number;
}

export const FilterChip = React.forwardRef<HTMLButtonElement, FilterChipProps>(
  ({ active = false, count, children, className, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      aria-pressed={active}
      className={cn(
        "inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-xs font-medium",
        "transition-colors active:scale-[0.97]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        "disabled:pointer-events-none disabled:opacity-50",
        active
          ? "border-brand bg-brand/15 text-foreground"
          : "border-border text-muted-foreground hover:bg-accent hover:text-foreground",
        className,
      )}
      {...props}
    >
      {children}
      {count != null ? <span className="tabular-nums text-[10px]">{count}</span> : null}
    </button>
  ),
);
FilterChip.displayName = "FilterChip";
