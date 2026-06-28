import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { useAgentStatus } from "@/hooks/useAgentStatus";
import * as api from "@/lib/api";
import type { ChatTab } from "@/lib/chats-storage";

const tab: ChatTab = {
  id: "1",
  agent: "lyra",
  harness: "omp-rpc",
  model: "omp-default",
  sessionId: null,
  streamToken: null,
  lastActive: 0,
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useAgentStatus", () => {
  it("fetches roster and harness-specific status", async () => {
    const spy = vi.spyOn(api, "fetchAgentStatus").mockImplementation(async (agent, harness) => {
      if (agent && harness) {
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
    const { result } = renderHook(() => useAgentStatus(tab), { wrapper });
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith();
      expect(spy).toHaveBeenCalledWith("lyra", "omp-rpc");
      expect(result.current.healthFor("lyra", "omp-rpc")?.online).toBe(true);
    });
  });
});
