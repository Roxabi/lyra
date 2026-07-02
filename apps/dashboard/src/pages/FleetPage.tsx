import { Badge, type BadgeVariant } from "@astryxdesign/core/Badge";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
} from "@astryxdesign/core/Table";
import { ShippingContainer } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { PageIntro } from "@/components/layout/PageIntro";
import { EmptyState } from "@/components/ui/empty-state";
import { FilterChip } from "@/components/ui/filter-chip";
import {
  ListToolbar,
  ListToolbarControls,
  ListToolbarHeader,
  ListToolbarSearch,
} from "@/components/ui/list-toolbar";
import { SortableTableHeader } from "@/components/ui/sortable-table-header";
import { type FleetRow, type FleetStatus, fetchFleet, type ImageDigestStatus } from "@/lib/api";
import { FLEET_STATUSES, type FleetSortKey, filterFleet, sortFleet } from "@/lib/fleet-filters";
import { type SortDirection, toggleSort } from "@/lib/sort";
import { useDebouncedValue } from "@/lib/use-debounced-value";

type StatusBadgeVariant = Extract<BadgeVariant, "success" | "error" | "neutral">;

function statusVariant(status: FleetStatus): StatusBadgeVariant {
  if (status === "ok") return "success";
  if (status === "stale") return "error";
  return "neutral"; // pinned + anything else
}

function digestVariant(status: ImageDigestStatus): StatusBadgeVariant {
  if (status === "current") return "success";
  if (status === "stale") return "error";
  return "neutral"; // n/a + anything else
}

function formatAge(ageS: number | null | undefined): string {
  if (ageS == null) return "—";
  if (ageS < 60) return `${Math.round(ageS)}s`;
  return `${Math.round(ageS / 60)}m`;
}

function FleetTableSkeleton() {
  const { t } = useTranslation("common");
  return (
    <div role="status" className="space-y-2" aria-busy="true" aria-label={t("actions.loading")}>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex items-center gap-4 border-b border-border/30 py-2">
          <Skeleton width={128} height={16} />
          <Skeleton width={48} height={24} radius="rounded" />
          <Skeleton width={56} height={24} radius="rounded" />
          <Skeleton width={64} height={16} />
          <Skeleton width={160} height={16} />
        </div>
      ))}
    </div>
  );
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
              {FLEET_STATUSES.map((status) => (
                <FilterChip
                  key={status}
                  active={statusFilters.includes(status)}
                  onClick={() => toggleStatusFilter(status)}
                >
                  {t(`fleet.status.${status}`)}
                </FilterChip>
              ))}
            </>
          }
        />
      </ListToolbar>

      {isLoading ? <FleetTableSkeleton /> : null}

      {!isLoading && visibleRows.length === 0 ? (
        <EmptyState
          icon={hasFilters ? undefined : ShippingContainer}
          title={hasFilters ? t("fleet.emptyFiltered") : t("fleet.empty")}
          hint={hasFilters ? undefined : t("fleet.emptyHint")}
        />
      ) : null}

      {!isLoading && visibleRows.length > 0 ? (
        <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
          <Table className="min-w-[820px]" dividers="rows" hasHover>
            <TableHeader>
              <TableRow isHeaderRow>
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
                <TableHeaderCell scope="col">{t("fleet.columns.imageDigest")}</TableHeaderCell>
                <SortableTableHeader
                  label={t("fleet.columns.health")}
                  active={sortKey === "health"}
                  direction={sortDirection}
                  onClick={() => onSort("health")}
                />
                <TableHeaderCell scope="col">{t("fleet.columns.image")}</TableHeaderCell>
                <TableHeaderCell scope="col">{t("fleet.columns.revision")}</TableHeaderCell>
                <SortableTableHeader
                  label={t("fleet.columns.age")}
                  active={sortKey === "age_s"}
                  direction={sortDirection}
                  onClick={() => onSort("age_s")}
                />
              </TableRow>
            </TableHeader>
            <TableBody>
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
                    <Badge
                      variant={statusVariant(row.status)}
                      label={t(`fleet.status.${row.status}`)}
                    />
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={digestVariant(row.image_digest_status)}
                      label={t(`fleet.imageDigest.${row.image_digest_status}`)}
                    />
                  </TableCell>
                  <TableCell className="capitalize text-muted-foreground">{row.health}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <div className="min-w-0 max-w-[220px] truncate">{row.image_ref}</div>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {row.image_revision ?? "—"}
                  </TableCell>
                  <TableCell className="text-muted-foreground tabular-nums">
                    {formatAge(row.age_s)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </div>
  );
}
