import { bffFetch } from "@/shared/api/bff-fetch";
import { parseBffResponse } from "@/shared/api/client";
import type { HarnessKind } from "@/shared/lib/chats-storage";

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
  const res = await bffFetch("/api/bff/agents");
  return parseBffResponse<{ agents: AgentSummary[] }>(res);
}

export async function createAgentConfig(body: {
  name: string;
  backend: HarnessKind;
  model: string;
  display_name?: string;
  tagline?: string;
}): Promise<AgentConfig> {
  const res = await bffFetch("/api/bff/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseBffResponse<AgentConfig>(res);
}

export async function fetchAgentConfig(name: string): Promise<AgentConfig> {
  const res = await bffFetch(`/api/bff/agents/${encodeURIComponent(name)}`);
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
    voice_json?: Record<string, unknown> | null;
  },
): Promise<AgentConfig> {
  const res = await bffFetch(`/api/bff/agents/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("agent patch failed");
  return res.json() as Promise<AgentConfig>;
}

export interface VoiceEngineInfo {
  name: string;
  supports_voice: boolean;
  supports_clone: boolean;
  vram_gib_est: number | null;
}

export interface VoiceIdInfo {
  voice_id: string;
  name: string;
  language?: string | null;
  gender?: string | null;
  engine?: string | null;
}

export interface VoiceSampleInfo {
  id: string;
  store_key: string | null;
  filename: string;
  cached: boolean;
}

export interface VoiceCapabilities {
  tts: {
    engines: VoiceEngineInfo[];
    samples: VoiceSampleInfo[];
    voices: VoiceIdInfo[];
    max_cached_engines: number;
    default_engine: string | null;
    catalog_revision: string | null;
  } | null;
  stt: {
    models: Record<string, string>[];
    default_model: string | null;
  } | null;
  error: string | null;
}

export async function fetchVoiceCapabilities(): Promise<VoiceCapabilities> {
  const res = await bffFetch("/api/bff/voice/capabilities");
  if (!res.ok) throw new Error("voice capabilities failed");
  return res.json() as Promise<VoiceCapabilities>;
}

export async function fetchAgentSoul(name: string): Promise<{
  sections: SoulSections;
  updated_at: string;
}> {
  const res = await bffFetch(`/api/bff/agents/${encodeURIComponent(name)}/soul`);
  if (!res.ok) throw new Error("soul get failed");
  return res.json() as Promise<{ sections: SoulSections; updated_at: string }>;
}

export async function putAgentSoul(name: string, body: { markdown: string }): Promise<unknown> {
  const res = await bffFetch(`/api/bff/agents/${encodeURIComponent(name)}/soul`, {
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
  const res = await bffFetch(`/api/bff/agents/${encodeURIComponent(name)}/soul/preview`, {
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
