import type { SortDirection } from "@/lib/sort";
import { cn } from "@/lib/utils";

export interface SortableTableHeaderProps {
  label: string;
  active: boolean;
  direction: SortDirection;
  onClick: () => void;
  className?: string;
}

export function SortableTableHeader({
  label,
  active,
  direction,
  onClick,
  className,
}: SortableTableHeaderProps) {
  return (
    <th
      className={cn("py-2 pr-3 font-medium text-muted-foreground", className)}
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={onClick}
        className="inline-flex min-h-9 items-center gap-1 hover:text-foreground active:scale-[0.98]"
      >
        {label}
        {active ? <span aria-hidden>{direction === "asc" ? "↑" : "↓"}</span> : null}
      </button>
    </th>
  );
}
