import type { HarnessKind } from "@/lib/chats-storage";
import { operatorAuthHeaders } from "@/lib/operator-auth";

export interface AgentHealth {
  agent: string;
  in_roster: boolean;
  harness: HarnessKind;
  harness_reachable: boolean;
  online: boolean;
}

export interface DashboardSession {
  session_id: string;
  pool_id: string;
  platform: "telegram" | "discord" | "web";
  cli_session_id: string | null;
  first_user_msg: string | null;
  turn_count: number;
  last_active_at: string;
}

export type FleetStatus = "ok" | "stale" | "unknown" | "pinned";
export type ImageDigestStatus = "current" | "stale" | "unknown_compare" | "n/a";

export interface FleetRow {
  container_name: string;
  host: string;
  component_key: string;
  image_ref: string;
  image_revision: string | null;
  health: string;
  status: FleetStatus;
  last_report_at: string | null;
  age_s: number | null;
  systemd_unit: string;
  instrumented: boolean;
  source: string;
  image_digest_status: ImageDigestStatus;
}

export async function fetchFleet(): Promise<FleetRow[]> {
  const res = await fetch("/api/bff/fleet");
  if (!res.ok) throw new Error("fleet fetch failed");
  const data = (await res.json()) as { rows: FleetRow[] };
  return data.rows;
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

export async function fetchSessions(agent: string): Promise<DashboardSession[]> {
  const res = await fetch(`/api/bff/sessions?agent=${encodeURIComponent(agent)}`);
  if (!res.ok) throw new Error("sessions fetch failed");
  const data = (await res.json()) as { sessions: DashboardSession[] };
  return data.sessions;
}

export interface DashboardTurn {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

export interface DashboardJob {
  job_id: string;
  pool_id: string;
  agent: string | null;
  platform: string | null;
  status: string;
  started_at: string;
  concurrency_mode: string;
  worker_loc: string | null;
  steer_subject: string;
}

export async function fetchJobs(): Promise<DashboardJob[]> {
  const res = await fetch("/api/bff/jobs");
  if (!res.ok) throw new Error("jobs fetch failed");
  const data = (await res.json()) as { jobs: DashboardJob[] };
  return data.jobs;
}

export async function launchJob(body: {
  agent: string;
  prompt: string;
  job_name?: string;
  model?: string | null;
}): Promise<{
  accepted: boolean;
  job_id: string;
  message: string;
  dispatch_subject: string;
}> {
  const res = await fetch("/api/bff/jobs/launch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("job launch failed");
  return res.json() as Promise<{
    accepted: boolean;
    job_id: string;
    message: string;
    dispatch_subject: string;
  }>;
}

export async function steerJob(
  jobId: string,
  text: string,
): Promise<{ accepted: boolean; message: string }> {
  const res = await fetch("/api/bff/jobs/steer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, text }),
  });
  if (!res.ok) throw new Error("job steer failed");
  return res.json() as Promise<{ accepted: boolean; message: string }>;
}

export type OpsLogPreset =
  | "hub-errors"
  | "operator-events"
  | "deploy-failures"
  | "container-journal";

export interface OpsEngineHealth {
  engine: "loki" | "langfuse" | "otel-collector";
  label: string;
  reachable: boolean;
  detail: string;
}

export interface OpsLogEntry {
  timestamp: string;
  line: string;
  labels: Record<string, string>;
}

export async function fetchOpsHealth(): Promise<OpsEngineHealth[]> {
  const res = await fetch("/api/bff/ops/health");
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
  const res = await fetch(`/api/bff/ops/logs?${params}`);
  if (!res.ok) throw new Error("ops logs fetch failed");
  return res.json() as Promise<{
    preset: OpsLogPreset;
    query: string;
    engine_reachable: boolean;
    entries: OpsLogEntry[];
  }>;
}

export async function fetchSessionTurns(sessionId: string): Promise<DashboardTurn[]> {
  const res = await fetch(`/api/bff/sessions/turns?session_id=${encodeURIComponent(sessionId)}`);
  if (!res.ok) throw new Error("turns fetch failed");
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
  if (!res.ok) throw new Error("resume failed");
  return res.json() as Promise<{ accepted: boolean; message: string }>;
}

export interface ConnectorDescriptor {
  name: string;
  family: string;
  label: string;
}

export interface ConnectorInstallation {
  connector: string;
  external_id: string;
  factory_tenant: string;
  enabled: boolean;
}

async function connectorFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, {
    ...init,
    headers: {
      ...operatorAuthHeaders(),
      ...(init?.headers ?? {}),
    },
  });
  if (res.status === 401) throw new Error("auth");
  return res;
}

export async function fetchConnectors(): Promise<{
  connectors: ConnectorDescriptor[];
  factory_tenant: string;
}> {
  const res = await connectorFetch("/api/bff/connectors");
  if (!res.ok) throw new Error("connectors fetch failed");
  return res.json() as Promise<{ connectors: ConnectorDescriptor[]; factory_tenant: string }>;
}

export async function fetchGithubInstallUrl(): Promise<{ url: string; app_slug: string }> {
  const res = await connectorFetch("/api/bff/connectors/github/install-url");
  if (!res.ok) throw new Error("github install url fetch failed");
  return res.json() as Promise<{ url: string; app_slug: string }>;
}

export async function fetchConnectorInstallations(
  connector: string,
): Promise<{ installations: ConnectorInstallation[] }> {
  const res = await connectorFetch(
    `/api/bff/connectors/${encodeURIComponent(connector)}/installations`,
  );
  if (!res.ok) throw new Error("installations fetch failed");
  return res.json() as Promise<{ installations: ConnectorInstallation[] }>;
}

export async function upsertConnectorInstallation(
  connector: string,
  body: { external_id: string; factory_tenant?: string },
): Promise<{ ok: boolean }> {
  const res = await connectorFetch(
    `/api/bff/connectors/${encodeURIComponent(connector)}/installations`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error("installation upsert failed");
  return res.json() as Promise<{ ok: boolean }>;
}

export async function deleteConnectorInstallation(
  connector: string,
  externalId: string,
): Promise<{ ok: boolean }> {
  const res = await connectorFetch(
    `/api/bff/connectors/${encodeURIComponent(connector)}/installations/${encodeURIComponent(externalId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error("installation delete failed");
  return res.json() as Promise<{ ok: boolean }>;
}

export const MODEL_CATALOG: Record<HarnessKind, string[]> = {
  "claude-cli": ["sonnet", "opus", "haiku"],
  "omp-rpc": ["omp-default", "omp-fast"],
};

/** First curated model for a harness — used when switching harness on a tab. */
export function defaultModelForHarness(harness: HarnessKind): string {
  const models = MODEL_CATALOG[harness];
  const first = models[0];
  if (!first) {
    throw new Error(`no models configured for harness ${harness}`);
  }
  return first;
}
