import type { HarnessKind } from "@/lib/chats-storage";

export interface AgentHealth {
  agent: string;
  in_roster: boolean;
  harness: HarnessKind;
  harness_reachable: boolean;
  online: boolean;
}

export async function fetchAgents(): Promise<string[]> {
  const res = await fetch("/api/agents");
  if (!res.ok) throw new Error("agents fetch failed");
  const data = (await res.json()) as { agents: string[] };
  return data.agents;
}

export async function fetchAgentStatus(
  agent?: string,
  harness?: HarnessKind,
): Promise<AgentHealth[]> {
  const params = new URLSearchParams();
  if (agent && harness) {
    params.set("agent", agent);
    params.set("harness", harness);
  }
  const qs = params.toString();
  const res = await fetch(`/api/bff/agents/status${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error("status fetch failed");
  const data = (await res.json()) as { agents: AgentHealth[] };
  return data.agents;
}

export async function postChat(body: {
  agent: string;
  text: string;
  session_id?: string | null;
  harness: HarnessKind;
  model: string;
}): Promise<{ session_id: string; stream_token: string }> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = res.status === 503 ? "adapter not ready" : "send failed";
    throw new Error(detail);
  }
  return res.json() as Promise<{ session_id: string; stream_token: string }>;
}

export function openChatStream(
  sessionId: string,
  streamToken: string,
  onEvent: (ev: { type: string; text?: string; message?: string }) => void,
): EventSource {
  const url = `/api/stream/${sessionId}?token=${encodeURIComponent(streamToken)}`;
  const source = new EventSource(url);
  source.onmessage = (msg) => {
    onEvent(JSON.parse(msg.data) as { type: string; text?: string; message?: string });
  };
  return source;
}

export const MODEL_CATALOG: Record<HarnessKind, string[]> = {
  "claude-cli": ["sonnet", "opus", "haiku"],
  "omp-rpc": ["omp-default", "omp-fast"],
};