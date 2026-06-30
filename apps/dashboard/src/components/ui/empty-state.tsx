import type { Icon } from "@phosphor-icons/react";
import type * as React from "react";
import { cn } from "@/lib/utils";

export function EmptyState({
  icon: IconComponent,
  title,
  hint,
  action,
  className,
}: {
  icon?: Icon;
  title: string;
  hint?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-dashed border-border bg-card px-4 py-10 text-center",
        className,
      )}
    >
      {IconComponent ? (
        <IconComponent
          className="mx-auto mb-3 size-10 text-muted-foreground"
          aria-hidden
          weight="thin"
        />
      ) : null}
      <p className="font-medium text-foreground">{title}</p>
      {hint ? <p className="mt-1 text-sm text-muted-foreground">{hint}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}