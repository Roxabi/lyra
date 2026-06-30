import { describe, expect, it } from "vitest";
import type { FleetRow } from "@/lib/api";
import { filterFleet, sortFleet } from "@/lib/fleet-filters";

const ROWS: FleetRow[] = [
  {
    container_name: "factory-b",
    host: "m1",
    component_key: "factory-b",
    status: "stale",
    health: "unhealthy",
    image_ref: "img:b",
    image_revision: "rev-b",
    age_s: 120,
    last_report_at: "2026-06-30T10:00:00Z",
    systemd_unit: "factory-b.service",
    instrumented: true,
    source: "nats",
  },
  {
    container_name: "factory-a",
    host: "m1",
    component_key: "factory-a",
    status: "ok",
    health: "healthy",
    image_ref: "img:a",
    image_revision: "rev-a",
    age_s: 30,
    last_report_at: "2026-06-30T11:00:00Z",
    systemd_unit: "factory-a.service",
    instrumented: true,
    source: "nats",
  },
];

describe("fleet-filters", () => {
  it("filters by status and search", () => {
    expect(filterFleet(ROWS, { search: "factory-a", statuses: [] })).toHaveLength(1);
    expect(filterFleet(ROWS, { search: "", statuses: ["ok"] })).toHaveLength(1);
  });

  it("sorts by age descending", () => {
    const sorted = sortFleet(ROWS, "age_s", "desc");
    expect(sorted[0]?.container_name).toBe("factory-b");
  });
});