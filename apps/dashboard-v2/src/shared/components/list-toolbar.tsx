import { Search } from "lucide-react";
import type { ReactNode } from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export function ListToolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <section
      className={cn(
        "@container space-y-3 rounded-xl border bg-card p-3 text-card-foreground shadow-sm sm:p-4",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function ListToolbarHeader({
  meta,
  actions,
  className,
}: {
  meta: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-b border-border/70 pb-3",
        className,
      )}
    >
      <div className="text-sm font-medium text-foreground">{meta}</div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </div>
  );
}

export function ListToolbarSearch({
  value,
  onChange,
  placeholder,
  "aria-label": ariaLabel,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  "aria-label": string;
  className?: string;
}) {
  return (
    <div className={cn("relative", className)}>
      <Search
        className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden
      />
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        className="pl-8"
      />
    </div>
  );
}

export function ListToolbarControls({
  filters,
  view,
  className,
}: {
  filters?: ReactNode;
  view?: ReactNode;
  className?: string;
}) {
  if (!filters && !view) return null;

  return (
    <div className={cn("flex w-full items-center gap-2", className)}>
      {filters ? <div className="flex min-w-0 shrink-0 items-center gap-2">{filters}</div> : null}
      {view ? (
        <div className="ml-auto flex shrink-0 items-center justify-end gap-2 [&_[role=group]]:inline-flex">
          {view}
        </div>
      ) : null}
    </div>
  );
}
