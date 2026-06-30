import type { FleetRow, FleetStatus } from "@/lib/api";
import { compareNumbers, compareStrings, type SortDirection } from "@/lib/sort";

export type FleetSortKey = "container_name" | "status" | "health" | "age_s";

export const FLEET_STATUSES: FleetStatus[] = ["ok", "stale", "unknown", "pinned"];

export function filterFleet(
  rows: FleetRow[],
  { search, statuses }: { search: string; statuses: FleetStatus[] },
): FleetRow[] {
  const q = search.trim().toLowerCase();
  return rows.filter((row) => {
    if (statuses.length > 0 && !statuses.includes(row.status)) return false;
    if (!q) return true;
    const haystack = [
      row.container_name,
      row.status,
      row.health,
      row.image_ref,
      row.image_revision ?? "",
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });
}

export function sortFleet(
  rows: FleetRow[],
  sortKey: FleetSortKey,
  direction: SortDirection,
): FleetRow[] {
  const sorted = [...rows];
  sorted.sort((a, b) => {
    switch (sortKey) {
      case "container_name":
        return compareStrings(a.container_name, b.container_name, direction);
      case "status":
        return compareStrings(a.status, b.status, direction);
      case "health":
        return compareStrings(a.health, b.health, direction);
      case "age_s":
        return compareNumbers(a.age_s, b.age_s, direction);
      default:
        return 0;
    }
  });
  return sorted;
}