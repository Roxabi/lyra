import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { FleetPage } from "@/pages/FleetPage";

function renderFleet() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FleetPage />
    </QueryClientProvider>,
  );
}

describe("FleetPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchFleet").mockResolvedValue([
      {
        container_name: "factory-hub",
        host: "roxabituwer",
        component_key: "hub",
        image_ref: "ghcr.io/roxabi/factory:staging-svc",
        image_revision: "abc",
        health: "healthy",
        status: "ok",
        last_report_at: "2026-06-29T12:00:00+00:00",
        age_s: 10,
        systemd_unit: "factory-hub.service",
        instrumented: true,
        source: "live",
      },
      {
        container_name: "factory-loki",
        host: "",
        component_key: "loki",
        image_ref: "grafana/loki:3",
        image_revision: null,
        health: "unknown",
        status: "unknown",
        last_report_at: null,
        age_s: null,
        systemd_unit: "factory-loki.service",
        instrumented: false,
        source: "manifest",
      },
      {
        container_name: "factory-clipool",
        host: "",
        component_key: "clipool",
        image_ref: "ghcr.io/roxabi/factory:staging",
        image_revision: null,
        health: "healthy",
        status: "stale",
        last_report_at: null,
        age_s: 95,
        systemd_unit: "factory-clipool.service",
        instrumented: true,
        source: "manifest",
      },
    ]);
  });

  it("renders fleet rows with status badges", async () => {
    renderFleet();
    await waitFor(() => {
      expect(screen.getByText("factory-hub")).toBeTruthy();
    });
    expect(screen.getByText("factory-loki")).toBeTruthy();
    expect(screen.getByText("factory-clipool")).toBeTruthy();
  });
});
