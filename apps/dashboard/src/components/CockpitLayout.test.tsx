import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CockpitLayout } from "@/components/CockpitLayout";
import * as api from "@/lib/api";

function renderCockpit() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CockpitLayout />
    </QueryClientProvider>,
  );
}

describe("CockpitLayout", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchAgents").mockResolvedValue(["lyra"]);
    vi.spyOn(api, "fetchAgentStatus").mockResolvedValue([
      {
        agent: "lyra",
        in_roster: true,
        harness: "claude-cli",
        harness_reachable: true,
        online: true,
      },
    ]);
    vi.spyOn(api, "fetchSessions").mockResolvedValue([]);
  });

  it("renders cockpit grid with reprendre and panel stubs", async () => {
    renderCockpit();
    await waitFor(() => {
      expect(screen.getByText("Reprendre")).toBeTruthy();
    });
    expect(screen.getByText("Jobs (#1772)")).toBeTruthy();
    expect(screen.getByText("Obs (#1774)")).toBeTruthy();
  });

  it("fetches agent status with active tab harness", async () => {
    const statusSpy = vi.spyOn(api, "fetchAgentStatus");
    renderCockpit();
    await waitFor(() => {
      expect(statusSpy).toHaveBeenCalled();
    });
    expect(statusSpy.mock.calls[0]).toEqual(["lyra", "claude-cli"]);
  });
});
