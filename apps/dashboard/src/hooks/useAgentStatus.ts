import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { type AgentHealth, fetchAgentStatus } from "@/lib/api";
import type { ChatTab } from "@/lib/chats-storage";

export function useAgentStatus(activeTab: ChatTab | undefined) {
  const { data: status = [] } = useQuery({
    queryKey: ["agent-status", activeTab?.agent, activeTab?.harness],
    queryFn: () => fetchAgentStatus(activeTab?.agent, activeTab?.harness),
    enabled: Boolean(activeTab?.agent),
    refetchInterval: 30_000,
  });

  const healthFor = useCallback(
    (agent: string, harness?: ChatTab["harness"]): AgentHealth | undefined =>
      status.find((h) => h.agent === agent && (!harness || h.harness === harness)),
    [status],
  );

  return { status, healthFor };
}
