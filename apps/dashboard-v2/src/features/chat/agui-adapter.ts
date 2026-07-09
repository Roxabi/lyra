import type { ModelMessage, StreamChunk, TextPart, UIMessage } from "@tanstack/ai/client";
import type { ConnectConnectionAdapter } from "@tanstack/ai-react";
import { postChat } from "@/features/chat/api";
import type { HarnessKind } from "@/shared/lib/chats-storage";
import { randomId } from "@/shared/lib/chats-storage";

export interface AguiTabConfig {
  agent: string;
  harness: HarnessKind;
  model: string;
  sessionId: string | null;
}

function textFromUiMessage(message: UIMessage): string {
  return message.parts
    .filter((part): part is TextPart => part.type === "text")
    .map((part) => part.content)
    .join("");
}

function textFromModelMessage(message: ModelMessage): string {
  if (typeof message.content === "string") return message.content;
  if (!Array.isArray(message.content)) return "";
  return message.content
    .filter((part): part is TextPart => part.type === "text")
    .map((part) => part.content)
    .join("");
}

function lastUserText(messages: Array<UIMessage> | Array<ModelMessage>): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message.role !== "user") continue;
    if ("parts" in message) return textFromUiMessage(message);
    return textFromModelMessage(message);
  }
  return "";
}

/** Parse SSE frames from an incremental buffer; returns parsed chunks and leftover bytes. */
export function parseSseBuffer(buffer: string): { events: StreamChunk[]; rest: string } {
  const events: StreamChunk[] = [];
  const frames = buffer.split("\n\n");
  const rest = frames.pop() ?? "";

  for (const frame of frames) {
    for (const line of frame.split("\n")) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      events.push(JSON.parse(payload) as StreamChunk);
    }
  }

  return { events, rest };
}

export function makeAguiAdapter(
  getTab: () => AguiTabConfig,
  onSession: (patch: { sessionId: string; streamToken: string }) => void,
): ConnectConnectionAdapter {
  return {
    async *connect(messages, _data, abortSignal) {
      const tab = getTab();
      const text = lastUserText(messages);
      if (!text.trim()) return;

      const sessionId = tab.sessionId ?? randomId();
      const { session_id, stream_token } = await postChat({
        agent: tab.agent,
        text,
        session_id: sessionId,
        harness: tab.harness,
        model: tab.model,
      });
      onSession({ sessionId: session_id, streamToken: stream_token });

      const streamUrl = `/api/stream/${encodeURIComponent(session_id)}?token=${encodeURIComponent(stream_token)}&format=agui`;
      const response = await fetch(streamUrl, { signal: abortSignal });
      if (!response.ok) {
        throw new Error(`stream failed: HTTP ${response.status}`);
      }
      if (!response.body) {
        throw new Error("stream response has no body");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (!abortSignal?.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const { events, rest } = parseSseBuffer(buffer);
          buffer = rest;
          for (const event of events) {
            yield event;
          }
        }
      } finally {
        reader.releaseLock();
      }
    },
  };
}
