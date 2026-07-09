import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Container } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("dashboard");
  const { t: tc } = useTranslation("common");
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
      <PageIntro>{t("fleet.subtitle")}</PageIntro>

      {isError ? (
        <p className="text-sm text-destructive" role="alert">
          {t("fleet.loadError")}
        </p>
      ) : null}

      <ListToolbar>
        <ListToolbarHeader
          meta={isLoading ? tc("actions.loading") : t("fleet.count", { count: visibleRows.length })}
        />
        <ListToolbarSearch
          value={search}
          onChange={setSearch}
          placeholder={t("fleet.searchPlaceholder")}
          aria-label={tc("search")}
        />
        <ListToolbarControls
          filters={
            <>
              <span className="text-xs text-muted-foreground">{t("fleet.filters.status")}</span>
              {FLEET_STATUSES.map((fleetStatus) => (
                <FilterChip
                  key={fleetStatus}
                  label={t(`fleet.status.${fleetStatus}`)}
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
          title={hasFilters ? t("fleet.emptyFiltered") : t("fleet.empty")}
          description={hasFilters ? undefined : t("fleet.emptyHint")}
        />
      ) : null}

      {!isLoading && visibleRows.length > 0 ? (
        <DataTable>
          <DataTableHeader>
            <SortableTableHeader
              label={t("fleet.columns.name")}
              active={sortKey === "container_name"}
              direction={sortDirection}
              onClick={() => onSort("container_name")}
            />
            <SortableTableHeader
              label={t("fleet.columns.status")}
              active={sortKey === "status"}
              direction={sortDirection}
              onClick={() => onSort("status")}
            />
            <TableHead>{t("fleet.columns.imageDigest")}</TableHead>
            <SortableTableHeader
              label={t("fleet.columns.health")}
              active={sortKey === "health"}
              direction={sortDirection}
              onClick={() => onSort("health")}
            />
            <TableHead>{t("fleet.columns.image")}</TableHead>
            <TableHead>{t("fleet.columns.revision")}</TableHead>
            <SortableTableHeader
              label={t("fleet.columns.age")}
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
                  <Badge variant={statusVariant(row.status)}>
                    {t(`fleet.status.${row.status}`)}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={digestVariant(row.image_digest_status)}>
                    {t(`fleet.imageDigest.${row.image_digest_status}`)}
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
