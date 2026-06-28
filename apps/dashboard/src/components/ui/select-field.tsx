import { CaretDown } from "@phosphor-icons/react";
import * as React from "react";
import { cn } from "@/lib/utils";

export interface SelectFieldProps extends React.ComponentProps<"select"> {
  label?: string;
}

const SelectField = React.forwardRef<HTMLSelectElement, SelectFieldProps>(
  ({ className, label, children, ...props }, ref) => (
    <label className="relative inline-flex min-w-0 items-center">
      {label ? <span className="sr-only">{label}</span> : null}
      <select
        ref={ref}
        aria-label={label ?? props["aria-label"]}
        className={cn(
          "h-8 appearance-none rounded-md border border-input bg-background pl-2.5 pr-7 text-xs font-medium text-foreground transition-colors hover:bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <CaretDown
        className="pointer-events-none absolute right-2 size-3 text-muted-foreground"
        aria-hidden
      />
    </label>
  ),
);
SelectField.displayName = "SelectField";

export { SelectField };
