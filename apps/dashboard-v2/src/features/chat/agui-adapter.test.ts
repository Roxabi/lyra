import { describe, expect, it } from "vitest";
import { parseSseBuffer } from "@/features/chat/agui-adapter";

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
});
