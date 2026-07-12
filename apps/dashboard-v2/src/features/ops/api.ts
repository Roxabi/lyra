import { bffFetch } from "@/shared/api/bff-fetch";
import type {
  AgentHealth,
  OpsEngineHealth,
  OpsLogEntry,
  OpsLogPreset,
} from "@/shared/api/bff-types";

export async function fetchAgentStatus(agent?: string, harness?: string): Promise<AgentHealth[]> {
  const params = new URLSearchParams();
  if (agent && harness) {
    params.set("agent", agent);
    params.set("harness", harness);
  }
  const qs = params.toString();
  const res = await bffFetch(`/api/bff/agents/status${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error("status fetch failed");
  const data = (await res.json()) as { agents: AgentHealth[] };
  return data.agents;
}

export async function fetchOpsHealth(): Promise<OpsEngineHealth[]> {
  const res = await bffFetch("/api/bff/ops/health");
  if (!res.ok) throw new Error("ops health fetch failed");
  const data = (await res.json()) as { engines: OpsEngineHealth[] };
  return data.engines;
}

export async function fetchOpsLogs(
  preset: OpsLogPreset,
  limit = 50,
  container?: string,
): Promise<{
  preset: OpsLogPreset;
  query: string;
  engine_reachable: boolean;
  entries: OpsLogEntry[];
}> {
  const params = new URLSearchParams({ preset, limit: String(limit) });
  if (container) params.set("container", container);
  const res = await bffFetch(`/api/bff/ops/logs?${params}`);
  if (!res.ok) throw new Error("ops logs fetch failed");
  return res.json() as Promise<{
    preset: OpsLogPreset;
    query: string;
    engine_reachable: boolean;
    entries: OpsLogEntry[];
  }>;
}

export type { OpsLogPreset };
