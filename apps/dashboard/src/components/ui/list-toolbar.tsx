import { TextInput } from "@astryxdesign/core/TextInput";
import { Toolbar } from "@astryxdesign/core/Toolbar";
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
  label = "En-tête de liste",
  className,
}: {
  meta: React.ReactNode;
  actions?: React.ReactNode;
  label?: string;
  className?: string;
}) {
  return (
    <Toolbar
      label={label}
      className={cn("justify-between border-b border-border/70 pb-3", className)}
      startContent={<p className="text-sm font-medium text-foreground">{meta}</p>}
      endContent={actions ? <div className="shrink-0">{actions}</div> : undefined}
    />
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
  label = "Filtres et affichage",
  className,
}: {
  filters?: React.ReactNode;
  view?: React.ReactNode;
  label?: string;
  className?: string;
}) {
  if (!filters && !view) return null;

  return (
    <Toolbar
      label={label}
      className={cn("w-full justify-between", className)}
      startContent={
        filters ? (
          <div className="flex min-w-0 shrink-0 items-center gap-2">{filters}</div>
        ) : undefined
      }
      endContent={
        view ? (
          <div className="flex shrink-0 items-center justify-end gap-2 [&_[role=group]]:inline-flex">
            {view}
          </div>
        ) : undefined
      }
    />
  );
}
