import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { type AgentHealth, fetchAgentStatus } from "@/lib/api";
import type { ChatTab } from "@/lib/chats-storage";

export function useAgentStatus(activeTab: ChatTab | undefined) {
  const { data: status = [] } = useQuery({
    queryKey: ["agent-status-all"],
    queryFn: () => fetchAgentStatus(),
    refetchInterval: 30_000,
  });

  const tabAgent = activeTab?.agent;
  const tabHarness = activeTab?.harness;

  const { data: tabHealth } = useQuery({
    queryKey: ["agent-status", tabAgent, tabHarness],
    queryFn: () => fetchAgentStatus(tabAgent ?? "", tabHarness ?? "claude-cli"),
    enabled: Boolean(tabAgent && tabHarness),
    refetchInterval: 30_000,
    select: (rows) => rows[0],
  });

  const healthFor = useCallback(
    (agent: string, harness?: ChatTab["harness"]): AgentHealth | undefined => {
      if (
        activeTab?.agent === agent &&
        activeTab.harness === harness &&
        tabHealth &&
        tabHealth.harness === harness
      ) {
        return tabHealth;
      }
      return status.find((h) => h.agent === agent);
    },
    [status, tabHealth, activeTab],
  );

  return { status, healthFor };
}
