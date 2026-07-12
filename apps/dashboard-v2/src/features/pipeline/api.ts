import { bffFetch } from "@/shared/api/bff-fetch";
import type { PipelineRun, PipelineStreamEvent } from "@/shared/api/bff-types";

export async function postPipelineStreamToken(): Promise<{ stream_token: string }> {
  const res = await bffFetch("/api/bff/pipeline/stream-token", { method: "POST" });
  if (!res.ok) throw new Error("pipeline stream token failed");
  return res.json() as Promise<{ stream_token: string }>;
}

export function openPipelineStream(
  streamToken: string,
  onEvent: (ev: PipelineStreamEvent) => void,
): EventSource {
  const url = `/api/bff/pipeline/stream?token=${encodeURIComponent(streamToken)}`;
  const source = new EventSource(url);
  source.onmessage = (msg) => {
    onEvent(JSON.parse(msg.data) as PipelineStreamEvent);
  };
  return source;
}

export type { PipelineRun };
