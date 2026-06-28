import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { ChatPage } from "@/pages/ChatPage";

function renderChat() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rootRoute = createRootRoute({
    component: ChatPage,
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

describe("ChatPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchAgents").mockResolvedValue(["lyra_default", "aryl_default"]);
    vi.spyOn(api, "fetchAgentStatus").mockResolvedValue([
      {
        agent: "lyra_default",
        in_roster: true,
        harness: "claude-cli",
        harness_reachable: true,
        online: true,
      },
      {
        agent: "aryl_default",
        in_roster: true,
        harness: "claude-cli",
        harness_reachable: true,
        online: true,
      },
    ]);
    vi.spyOn(api, "fetchSessions").mockResolvedValue([]);
    vi.spyOn(api, "fetchJobs").mockResolvedValue([]);
  });

  it("renders chat sidebar with reprendre section", async () => {
    renderChat();
    await waitFor(() => {
      expect(screen.getByText("Reprendre")).toBeTruthy();
    });
    expect(screen.getByText("Chats actifs")).toBeTruthy();
  });
});
