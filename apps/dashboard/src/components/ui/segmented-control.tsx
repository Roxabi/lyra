import { cn } from "@/lib/utils";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: import("@phosphor-icons/react").Icon;
}

export interface SegmentedControlProps<T extends string> {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel?: string;
  /** Icons only, or responsive (icons in narrow @container, labels from @md). */
  compact?: boolean | "responsive";
  className?: string;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  compact = false,
  className,
}: SegmentedControlProps<T>) {
  return (
    <fieldset
      className={cn(
        "m-0 inline-flex h-10 min-w-0 items-center rounded-lg border border-border/70 bg-muted p-1 shadow-sm",
        className,
      )}
    >
      {ariaLabel ? <legend className="sr-only">{ariaLabel}</legend> : null}
      {options.map((option) => {
        const isActive = option.value === value;
        const Icon = option.icon;
        const iconOnly = compact === true || compact === "responsive";
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={isActive}
            aria-label={iconOnly ? option.label : undefined}
            title={iconOnly ? option.label : undefined}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex h-8 items-center rounded-md text-sm",
              iconOnly ? "justify-center gap-1.5 px-2.5 @md:px-3" : "gap-1.5 px-3",
              "transition-colors active:scale-[0.98]",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              "disabled:pointer-events-none disabled:opacity-50",
              isActive
                ? "bg-card text-foreground shadow-sm ring-1 ring-border/60"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            {Icon ? <Icon className="size-4 shrink-0" aria-hidden /> : null}
            {compact === "responsive" ? (
              <span className="hidden @md:inline">{option.label}</span>
            ) : compact ? null : (
              option.label
            )}
          </button>
        );
      })}
    </fieldset>
  );
}
