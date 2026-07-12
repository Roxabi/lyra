import { bffFetch } from "@/shared/api/bff-fetch";
import type { DashboardJob, JobsStreamEvent } from "@/shared/api/bff-types";

export async function fetchAgents(): Promise<string[]> {
  const res = await bffFetch("/api/agents");
  if (!res.ok) throw new Error("agents fetch failed");
  const data = (await res.json()) as { agents: string[] };
  return data.agents;
}

export async function fetchAgentStatus(): Promise<import("@/shared/api/bff-types").AgentHealth[]> {
  const res = await bffFetch("/api/bff/agents/status");
  if (!res.ok) throw new Error("status fetch failed");
  const data = (await res.json()) as { agents: import("@/shared/api/bff-types").AgentHealth[] };
  return data.agents;
}

export async function postJobsStreamToken(): Promise<{ stream_token: string }> {
  const res = await bffFetch("/api/bff/jobs/stream-token", { method: "POST" });
  if (!res.ok) throw new Error("jobs stream token failed");
  return res.json() as Promise<{ stream_token: string }>;
}

export function openJobsStream(
  streamToken: string,
  onEvent: (ev: JobsStreamEvent) => void,
): EventSource {
  const url = `/api/bff/jobs/stream?token=${encodeURIComponent(streamToken)}`;
  const source = new EventSource(url);
  source.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as JobsStreamEvent);
    } catch {
      onEvent({ type: "error", message: "malformed frame" });
    }
  };
  return source;
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
  const res = await bffFetch("/api/bff/jobs/launch", {
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
  const res = await bffFetch("/api/bff/jobs/steer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, text }),
  });
  if (!res.ok) throw new Error("job steer failed");
  return res.json() as Promise<{ accepted: boolean; message: string }>;
}

export async function cancelJob(jobId: string): Promise<{ accepted: boolean; message: string }> {
  const res = await bffFetch("/api/bff/jobs/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId }),
  });
  if (!res.ok) throw new Error("job cancel failed");
  return res.json() as Promise<{ accepted: boolean; message: string }>;
}

export type { DashboardJob };
