import type { UIMessage } from "@tanstack/ai/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeAguiAdapter, parseSseBuffer } from "@/features/chat/agui-adapter";

describe("parseSseBuffer", () => {
  it("parses complete SSE data frames", () => {
    const buffer =
      'data: {"type":"RUN_STARTED","threadId":"s1","runId":"r1"}\n\n' +
      'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"m1","delta":"hi"}\n\n';
    const { events, rest } = parseSseBuffer(buffer);
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ type: "RUN_STARTED", threadId: "s1" });
    expect(events[1]).toMatchObject({ type: "TEXT_MESSAGE_CONTENT", delta: "hi" });
    expect(rest).toBe("");
  });

  it("keeps partial frames in the remainder buffer", () => {
    const buffer = 'data: {"type":"RUN_FINISHED","threadId":"s1","runId":"r1"}';
    const { events, rest } = parseSseBuffer(buffer);
    expect(events).toHaveLength(0);
    expect(rest).toBe(buffer);
  });

  it("ignores SSE comment keepalives", () => {
    const buffer = ": ping\n\n";
    const { events, rest } = parseSseBuffer(buffer);
    expect(events).toHaveLength(0);
    expect(rest).toBe("");
  });

  it("skips malformed JSON data frames", () => {
    const buffer = "data: not-json\n\n" + 'data: {"type":"RUN_FINISHED"}\n\n';
    const { events, rest } = parseSseBuffer(buffer);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "RUN_FINISHED" });
    expect(rest).toBe("");
  });

  it("parses trailing frame without final double newline", () => {
    const buffer = 'data: {"type":"RUN_FINISHED","threadId":"s1","runId":"r1"}';
    const first = parseSseBuffer(`${buffer}\n\n`);
    expect(first.events).toHaveLength(1);
    const partial = parseSseBuffer(buffer);
    expect(partial.events).toHaveLength(0);
    expect(partial.rest).toBe(buffer);
  });
});

describe("makeAguiAdapter connect", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("yields stream chunks from fetch body", async () => {
    const sse =
      'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"m1","delta":"hi"}\n\n' +
      'data: {"type":"RUN_FINISHED","threadId":"s1","runId":"r1"}\n\n';
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ session_id: "s1", stream_token: "tok" }),
        })
        .mockResolvedValueOnce({
          ok: true,
          body: new ReadableStream({
            start(controller) {
              controller.enqueue(new TextEncoder().encode(sse));
              controller.close();
            },
          }),
        }),
    );

    const adapter = makeAguiAdapter(
      () => ({
        tabId: "tab-1",
        agent: "lyra",
        harness: "claude-cli",
        model: "sonnet",
        sessionId: null,
      }),
      vi.fn(),
    );

    const messages: UIMessage[] = [
      {
        id: "u1",
        role: "user",
        parts: [{ type: "text", content: "hello" }],
      },
    ];
    const events = [];
    for await (const chunk of adapter.connect(messages, undefined, new AbortController().signal)) {
      events.push(chunk);
    }
    expect(events.some((e) => e.type === "TEXT_MESSAGE_CONTENT")).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
