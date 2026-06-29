import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as agentsApi from "@/lib/agents-api";
import { AgentsListPage } from "@/pages/AgentsPage";

function renderAgentsList() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({
    component: AgentsListPage,
  });
  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: ["/"] }),
    context: { queryClient: undefined as unknown as QueryClient },
  });
  void router.load();
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("AgentsListPage", () => {
  beforeEach(() => {
    vi.spyOn(agentsApi, "fetchAgentsConfigList").mockResolvedValue({
      agents: [
        {
          name: "lyra",
          backend: "claude-cli",
          model: "sonnet",
          updated_at: "2026-06-29T12:00:00Z",
          soul_document_bytes: 1200,
          has_soul: true,
        },
      ],
    });
  });

  it("renders agent list with harness and model", async () => {
    renderAgentsList();
    await waitFor(() => {
      expect(screen.getByText("lyra")).toBeTruthy();
    });
    expect(screen.getByText(/claude-cli/)).toBeTruthy();
    expect(screen.getByText(/sonnet/)).toBeTruthy();
  });
});
