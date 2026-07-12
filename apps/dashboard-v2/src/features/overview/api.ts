import { bffFetch } from "@/shared/api/bff-fetch";
import type { AgentHealth, DashboardJob, OpsEngineHealth } from "@/shared/api/bff-types";

export async function fetchAgents(): Promise<string[]> {
  const res = await bffFetch("/api/agents");
  if (!res.ok) throw new Error("agents fetch failed");
  const data = (await res.json()) as { agents: string[] };
  return data.agents;
}

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

export async function fetchJobs(): Promise<DashboardJob[]> {
  const res = await bffFetch("/api/bff/jobs");
  if (!res.ok) throw new Error("jobs fetch failed");
  const data = (await res.json()) as { jobs: DashboardJob[] };
  return data.jobs;
}

export async function fetchOpsHealth(): Promise<OpsEngineHealth[]> {
  const res = await bffFetch("/api/bff/ops/health");
  if (!res.ok) throw new Error("ops health fetch failed");
  const data = (await res.json()) as { engines: OpsEngineHealth[] };
  return data.engines;
}
