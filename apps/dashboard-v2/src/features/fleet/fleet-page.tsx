import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Container } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { TableCell, TableHead, TableRow } from "@/components/ui/table";
import { fetchFleet } from "@/features/fleet/api";
import type { FleetRow, FleetStatus, ImageDigestStatus } from "@/shared/api/bff-types";
import {
  DataTable,
  DataTableBody,
  DataTableHeader,
  TableRowsSkeleton,
} from "@/shared/components/data-table";
import { EmptyState } from "@/shared/components/empty-state";
import { FilterChip } from "@/shared/components/filter-chip";
import {
  ListToolbar,
  ListToolbarControls,
  ListToolbarHeader,
  ListToolbarSearch,
} from "@/shared/components/list-toolbar";
import { PageIntro } from "@/shared/components/page-intro";
import { SortableTableHeader } from "@/shared/components/sortable-table-header";
import { useDebouncedValue } from "@/shared/hooks/use-debounced-value";
import {
  FLEET_STATUSES,
  type FleetSortKey,
  filterFleet,
  sortFleet,
} from "@/shared/lib/fleet-filters";
import { type SortDirection, toggleSort } from "@/shared/lib/sort";

const STATUS_LABELS: Record<FleetStatus, string> = {
  ok: "OK",
  stale: "Stale",
  unknown: "Unknown",
  pinned: "Pinned",
};

const DIGEST_LABELS: Record<ImageDigestStatus, string> = {
  current: "Current",
  stale: "Stale",
  unknown_compare: "Unknown",
  "n/a": "N/A",
};

function statusVariant(status: FleetStatus): "default" | "destructive" | "outline" {
  if (status === "ok") return "default";
  if (status === "stale") return "destructive";
  return "outline";
}

function digestVariant(status: ImageDigestStatus): "default" | "destructive" | "outline" {
  if (status === "current") return "default";
  if (status === "stale") return "destructive";
  return "outline";
}

function formatAge(ageS: number | null | undefined): string {
  if (ageS == null) return "—";
  if (ageS < 60) return `${Math.round(ageS)}s`;
  return `${Math.round(ageS / 60)}m`;
}

export function FleetPage() {
  const [search, setSearch] = useState("");
  const [statusFilters, setStatusFilters] = useState<FleetStatus[]>([]);
  const [sortKey, setSortKey] = useState<FleetSortKey>("container_name");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const debouncedSearch = useDebouncedValue(search, 300);

  const {
    data: rows = [],
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["fleet"],
    queryFn: fetchFleet,
    refetchInterval: 30_000,
  });

  const hasFilters = search.trim().length > 0 || statusFilters.length > 0;

  const visibleRows = useMemo(() => {
    const filtered = filterFleet(rows, { search: debouncedSearch, statuses: statusFilters });
    return sortFleet(filtered, sortKey, sortDirection);
  }, [rows, debouncedSearch, statusFilters, sortKey, sortDirection]);

  function onSort(nextKey: FleetSortKey) {
    const next = toggleSort(sortKey, sortDirection, nextKey);
    setSortKey(next.key);
    setSortDirection(next.direction);
  }

  function toggleStatusFilter(status: FleetStatus) {
    setStatusFilters((prev) =>
      prev.includes(status) ? prev.filter((s) => s !== status) : [...prev, status],
    );
  }

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>Container fleet status — image revisions, health, and staleness.</PageIntro>

      {isError ? (
        <p className="text-sm text-destructive" role="alert">
          Failed to load fleet data.
        </p>
      ) : null}

      <ListToolbar>
        <ListToolbarHeader
          meta={
            isLoading
              ? "Loading…"
              : `${visibleRows.length} container${visibleRows.length === 1 ? "" : "s"}`
          }
        />
        <ListToolbarSearch
          value={search}
          onChange={setSearch}
          placeholder="Search containers…"
          aria-label="Search"
        />
        <ListToolbarControls
          filters={
            <>
              <span className="text-xs text-muted-foreground">Status</span>
              {FLEET_STATUSES.map((fleetStatus) => (
                <FilterChip
                  key={fleetStatus}
                  label={STATUS_LABELS[fleetStatus]}
                  active={statusFilters.includes(fleetStatus)}
                  onToggle={() => toggleStatusFilter(fleetStatus)}
                />
              ))}
            </>
          }
        />
      </ListToolbar>

      {isLoading ? <TableRowsSkeleton rows={4} cols={7} /> : null}

      {!isLoading && visibleRows.length === 0 ? (
        <EmptyState
          icon={hasFilters ? undefined : Container}
          title={hasFilters ? "No matching containers" : "No fleet data"}
          description={hasFilters ? undefined : "Fleet reports will appear when agents report in."}
        />
      ) : null}

      {!isLoading && visibleRows.length > 0 ? (
        <DataTable>
          <DataTableHeader>
            <SortableTableHeader
              label="Name"
              active={sortKey === "container_name"}
              direction={sortDirection}
              onClick={() => onSort("container_name")}
            />
            <SortableTableHeader
              label="Status"
              active={sortKey === "status"}
              direction={sortDirection}
              onClick={() => onSort("status")}
            />
            <TableHead>Image digest</TableHead>
            <SortableTableHeader
              label="Health"
              active={sortKey === "health"}
              direction={sortDirection}
              onClick={() => onSort("health")}
            />
            <TableHead>Image</TableHead>
            <TableHead>Revision</TableHead>
            <SortableTableHeader
              label="Age"
              active={sortKey === "age_s"}
              direction={sortDirection}
              onClick={() => onSort("age_s")}
            />
          </DataTableHeader>
          <DataTableBody>
            {visibleRows.map((row: FleetRow) => (
              <TableRow key={row.container_name}>
                <TableCell className="font-mono text-xs">
                  <Link
                    to="/ops"
                    search={{ container: row.container_name }}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {row.container_name}
                  </Link>
                </TableCell>
                <TableCell>
                  <Badge variant={statusVariant(row.status)}>{STATUS_LABELS[row.status]}</Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={digestVariant(row.image_digest_status)}>
                    {DIGEST_LABELS[row.image_digest_status]}
                  </Badge>
                </TableCell>
                <TableCell className="text-muted-foreground capitalize">{row.health}</TableCell>
                <TableCell className="max-w-56 truncate text-xs text-muted-foreground">
                  {row.image_ref}
                </TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">
                  {row.image_revision ?? "—"}
                </TableCell>
                <TableCell className="text-muted-foreground tabular-nums">
                  {formatAge(row.age_s)}
                </TableCell>
              </TableRow>
            ))}
          </DataTableBody>
        </DataTable>
      ) : null}
    </div>
  );
}
