import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { OpsPage } from "@/pages/OpsPage";

function renderOps() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OpsPage />
    </QueryClientProvider>,
  );
}

describe("OpsPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchAgentStatus").mockResolvedValue([
      {
        agent: "lyra",
        in_roster: true,
        harness: "claude-cli",
        harness_reachable: true,
        online: true,
      },
    ]);
    vi.spyOn(api, "fetchOpsHealth").mockResolvedValue([
      {
        engine: "loki",
        label: "Loki",
        reachable: true,
        detail: "ok",
      },
      {
        engine: "langfuse",
        label: "Langfuse",
        reachable: true,
        detail: "ok",
      },
      {
        engine: "otel-collector",
        label: "OTel Collector",
        reachable: false,
        detail: "timeout",
      },
    ]);
    vi.spyOn(api, "fetchOpsLogs").mockResolvedValue({
      preset: "hub-errors",
      query: '{job="factory-journal"} |= "ERROR"',
      engine_reachable: true,
      entries: [
        {
          timestamp: "2026-06-28T12:00:00+00:00",
          line: "ERROR factory.core.hub: crash",
          labels: { job: "factory-journal" },
        },
      ],
    });
  });

  it("renders engine health and log entries", async () => {
    renderOps();
    await waitFor(() => {
      expect(screen.getByText("Loki")).toBeTruthy();
    });
    expect(screen.getAllByText("En ligne").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("ERROR factory.core.hub: crash")).toBeTruthy();
    expect(screen.getByText("Lyra")).toBeTruthy();
  });
});
