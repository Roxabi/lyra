import { ChatApiError } from "@/features/chat/chat-errors";
import type { AgentHealth, DashboardSession, DashboardTurn } from "@/shared/api/bff-types";
import type { HarnessKind } from "@/shared/lib/chats-storage";

export async function fetchAgents(): Promise<string[]> {
  const res = await fetch("/api/agents");
  if (!res.ok) throw new ChatApiError("agents_fetch_failed");
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
  if (!res.ok) throw new ChatApiError("status_fetch_failed");
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
  const res = await fetch("/api/chat?format=agui", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ChatApiError(res.status === 503 ? "adapter_not_ready" : "send_failed");
  }
  return res.json() as Promise<{ session_id: string; stream_token: string }>;
}

export async function fetchSessions(agent: string): Promise<DashboardSession[]> {
  const res = await fetch(`/api/bff/sessions?agent=${encodeURIComponent(agent)}`);
  if (!res.ok) throw new ChatApiError("sessions_fetch_failed");
  const data = (await res.json()) as { sessions: DashboardSession[] };
  return data.sessions;
}

export async function fetchSessionTurns(sessionId: string): Promise<DashboardTurn[]> {
  const res = await fetch(`/api/bff/sessions/turns?session_id=${encodeURIComponent(sessionId)}`);
  if (!res.ok) throw new ChatApiError("turns_fetch_failed");
  const data = (await res.json()) as { turns: DashboardTurn[] };
  return data.turns;
}

export async function resumeSession(
  agent: string,
  cliSessionId: string,
): Promise<{ accepted: boolean; message: string }> {
  const res = await fetch("/api/bff/sessions/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent, cli_session_id: cliSessionId }),
  });
  if (!res.ok) throw new ChatApiError("resume_failed");
  return res.json() as Promise<{ accepted: boolean; message: string }>;
}
