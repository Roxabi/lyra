import { TableHead } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { SortDirection } from "@/shared/lib/sort";

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
    <TableHead
      scope="col"
      className={className}
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "inline-flex min-h-9 items-center gap-1 hover:text-foreground active:scale-[0.98]",
        )}
      >
        {label}
        {active ? <span aria-hidden>{direction === "asc" ? "↑" : "↓"}</span> : null}
      </button>
    </TableHead>
  );
}
