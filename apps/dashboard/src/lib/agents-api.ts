import { parseBffResponse } from "@/lib/bff-api";
import type { HarnessKind } from "@/lib/chats-storage";

export interface AgentSummary {
  name: string;
  backend: HarnessKind;
  model: string;
  updated_at: string;
  soul_document_bytes: number | null;
  has_soul: boolean;
  has_telegram: boolean;
  has_discord: boolean;
  has_email: boolean;
}

export interface AgentConfig {
  name: string;
  backend: HarnessKind;
  model: string;
  voice_json: Record<string, unknown> | null;
  soul_meta_json: {
    header?: { display_name?: string; tagline?: string };
  } | null;
  soul_document_blob_ref: string | null;
  soul_document_bytes: number | null;
  updated_at: string;
}

export type SoulSections = Record<string, string>;

export async function fetchAgentsConfigList(): Promise<{ agents: AgentSummary[] }> {
  const res = await fetch("/api/bff/agents");
  return parseBffResponse<{ agents: AgentSummary[] }>(res);
}

export async function createAgentConfig(body: {
  name: string;
  backend: HarnessKind;
  model: string;
  display_name?: string;
  tagline?: string;
}): Promise<AgentConfig> {
  const res = await fetch("/api/bff/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseBffResponse<AgentConfig>(res);
}

export async function fetchAgentConfig(name: string): Promise<AgentConfig> {
  const res = await fetch(`/api/bff/agents/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error("agent get failed");
  return res.json() as Promise<AgentConfig>;
}

export async function patchAgentConfig(
  name: string,
  body: {
    backend?: HarnessKind;
    model?: string;
    display_name?: string;
    tagline?: string;
  },
): Promise<AgentConfig> {
  const res = await fetch(`/api/bff/agents/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("agent patch failed");
  return res.json() as Promise<AgentConfig>;
}

export async function fetchAgentSoul(name: string): Promise<{
  sections: SoulSections;
  updated_at: string;
}> {
  const res = await fetch(`/api/bff/agents/${encodeURIComponent(name)}/soul`);
  if (!res.ok) throw new Error("soul get failed");
  return res.json() as Promise<{ sections: SoulSections; updated_at: string }>;
}

export async function putAgentSoul(name: string, body: { markdown: string }): Promise<unknown> {
  const res = await fetch(`/api/bff/agents/${encodeURIComponent(name)}/soul`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("soul put failed");
  return res.json();
}

export async function previewAgentSoul(
  name: string,
  body: { sections: SoulSections },
): Promise<{ composed: string; truncated: boolean }> {
  const res = await fetch(`/api/bff/agents/${encodeURIComponent(name)}/soul/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("soul preview failed");
  return res.json() as Promise<{ composed: string; truncated: boolean }>;
}

export async function fetchAgentDefaults(name: string): Promise<{
  backend: HarnessKind;
  model: string;
}> {
  const cfg = await fetchAgentConfig(name);
  return { backend: cfg.backend, model: cfg.model };
}
