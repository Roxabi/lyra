import { TableHeaderCell } from "@astryxdesign/core/Table";
import type { SortDirection } from "@/lib/sort";

/**
 * Sortable column header — an Astryx `TableHeaderCell` (native `<th>` styled via
 * TableContext) wrapping the app's sort button + direction arrow. Must be
 * rendered inside an Astryx `Table`. Sort state stays app-driven (`useTableSortable`
 * is data-driven only); this keeps the existing tested sort logic.
 */
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
    <TableHeaderCell
      scope="col"
      className={className}
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
    </TableHeaderCell>
  );
}
