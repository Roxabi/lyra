import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { EmptyState } from "@/shared/components/empty-state";

interface DataTableProps {
  children: ReactNode;
  isLoading?: boolean;
  isEmpty?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  skeletonRows?: number;
  skeletonCols?: number;
  className?: string;
}

export function DataTable({
  children,
  isLoading,
  isEmpty,
  emptyTitle = "No data",
  emptyDescription,
  skeletonRows = 3,
  skeletonCols = 4,
  className,
}: DataTableProps) {
  if (isLoading) {
    return (
      <div className={cn("overflow-x-auto rounded-xl border bg-card shadow-sm", className)}>
        <TableRowsSkeleton rows={skeletonRows} cols={skeletonCols} />
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className={cn("rounded-xl border bg-card shadow-sm", className)}>
        <EmptyState title={emptyTitle} description={emptyDescription} />
      </div>
    );
  }

  return (
    <div className={cn("overflow-x-auto rounded-xl border bg-card shadow-sm", className)}>
      <Table>{children}</Table>
    </div>
  );
}

export function DataTableHeader({ children }: { children: ReactNode }) {
  return (
    <TableHeader>
      <TableRow>{children}</TableRow>
    </TableHeader>
  );
}

export function DataTableBody({ children }: { children: ReactNode }) {
  return <TableBody>{children}</TableBody>;
}

export function TableRowsSkeleton({ rows = 3, cols = 4 }: { rows?: number; cols?: number }) {
  const skeletonRows = Array.from({ length: rows }, (_, row) => ({
    id: `table-skel-${rows}x${cols}-r${row}`,
    cells: Array.from({ length: cols }, (_, col) => `table-skel-${rows}x${cols}-r${row}-c${col}`),
  }));

  return (
    <div role="status" className="divide-y px-4" aria-busy="true" aria-label="Loading">
      {skeletonRows.map((row) => (
        <div key={row.id} className="flex items-center gap-4 py-3">
          {row.cells.map((cellId) => (
            <Skeleton key={cellId} className="h-4 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function ListRowsSkeleton({ rows = 3 }: { rows?: number }) {
  const skeletonRows = Array.from({ length: rows }, (_, row) => `list-skel-${rows}-r${row}`);

  return (
    <div role="status" className="space-y-2" aria-busy="true" aria-label="Loading">
      {skeletonRows.map((rowId) => (
        <div key={rowId} className="flex items-center gap-3 rounded-md bg-muted/30 px-3 py-2">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="ml-auto h-6 w-14 rounded-full" />
        </div>
      ))}
    </div>
  );
}
