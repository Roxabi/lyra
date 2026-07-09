import type { FleetRow } from "@/shared/api/bff-types";

export async function fetchFleet(): Promise<FleetRow[]> {
  const res = await fetch("/api/bff/fleet");
  if (!res.ok) throw new Error("fleet fetch failed");
  const data = (await res.json()) as { rows: FleetRow[] };
  return data.rows;
}

export type { FleetRow };
