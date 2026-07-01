import { TextInput } from "@astryxdesign/core/TextInput";
import { MagnifyingGlass } from "@phosphor-icons/react";
import type * as React from "react";
import { cn } from "@/lib/utils";

export function ListToolbar({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "@container space-y-3 rounded-xl border border-border bg-card p-3 text-card-foreground shadow-sm sm:p-4",
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
  meta: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-b border-border/70 pb-3",
        className,
      )}
    >
      <p className="text-sm font-medium text-foreground">{meta}</p>
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
    <div className={cn(className)}>
      <TextInput
        label={ariaLabel}
        isLabelHidden
        value={value}
        onChange={(next) => onChange(next)}
        placeholder={placeholder}
        startIcon={<MagnifyingGlass className="size-4" aria-hidden />}
        width="100%"
      />
    </div>
  );
}

export function ListToolbarControls({
  filters,
  view,
  className,
}: {
  filters?: React.ReactNode;
  view?: React.ReactNode;
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
