import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { OpsPage } from "@/pages/OpsPage";

function renderOps() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rootRoute = createRootRoute();
  const opsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/ops",
    validateSearch: (search: Record<string, unknown>) => ({
      container:
        typeof search.container === "string" && search.container.trim()
          ? search.container.trim()
          : undefined,
    }),
    component: OpsPage,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([opsRoute]),
    history: createMemoryHistory({ initialEntries: ["/ops"] }),
    context: { queryClient: undefined as unknown as QueryClient },
  });
  void router.load();
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

function harnessCard(title: string) {
  const heading = screen.getByText(title);
  const card = heading.closest(".dashboard-surface");
  expect(card).not.toBeNull();
  return within(card as HTMLElement);
}

describe("OpsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    vi.spyOn(api, "fetchAgentStatus").mockImplementation(async (_agent, harness) => {
      if (harness === "omp-rpc") {
        return [
          {
            agent: "lyra",
            in_roster: true,
            harness: "omp-rpc",
            harness_reachable: true,
            online: true,
          },
        ];
      }
      return [
        {
          agent: "lyra",
          in_roster: true,
          harness: "claude-cli",
          harness_reachable: true,
          online: true,
        },
      ];
    });
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
    await waitFor(() => {
      expect(harnessCard("Clipool (claude-cli)").getByText("En ligne")).toBeTruthy();
    });
    expect(screen.getByText("ERROR factory.core.hub: crash")).toBeTruthy();
    expect(screen.getByText("Lyra")).toBeTruthy();
  });

  it("shows omp harness online when omp-rpc probe succeeds", async () => {
    const fetchAgentStatus = vi.mocked(api.fetchAgentStatus);
    renderOps();
    await waitFor(() => {
      expect(fetchAgentStatus).toHaveBeenCalledWith("lyra", "omp-rpc");
      expect(harnessCard("OMP (omp-rpc)").getByText("En ligne")).toBeTruthy();
    });
  });

  it("shows omp harness offline when omp-rpc probe fails", async () => {
    const fetchAgentStatus = vi.mocked(api.fetchAgentStatus);
    fetchAgentStatus.mockImplementation(async (_agent, harness) => {
      if (harness === "omp-rpc") {
        return [
          {
            agent: "lyra",
            in_roster: true,
            harness: "omp-rpc",
            harness_reachable: false,
            online: false,
          },
        ];
      }
      return [
        {
          agent: "lyra",
          in_roster: true,
          harness: "claude-cli",
          harness_reachable: true,
          online: true,
        },
      ];
    });

    renderOps();
    await waitFor(() => {
      expect(fetchAgentStatus).toHaveBeenCalledWith("lyra", "omp-rpc");
      expect(harnessCard("OMP (omp-rpc)").getByText("Hors ligne")).toBeTruthy();
      expect(harnessCard("Clipool (claude-cli)").getByText("En ligne")).toBeTruthy();
    });
  });

  it("shows omp offline and skips omp probe when roster is empty", async () => {
    const fetchAgentStatus = vi.mocked(api.fetchAgentStatus);
    fetchAgentStatus.mockResolvedValue([]);

    renderOps();
    await waitFor(() => {
      expect(harnessCard("OMP (omp-rpc)").getByText("Hors ligne")).toBeTruthy();
    });
    expect(fetchAgentStatus).toHaveBeenCalledTimes(1);
    expect(fetchAgentStatus).not.toHaveBeenCalledWith(expect.anything(), "omp-rpc");
  });
});
