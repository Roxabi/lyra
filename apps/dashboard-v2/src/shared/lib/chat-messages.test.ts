import { describe, expect, it } from "vitest";
import { parseChatLog, turnsToInitialMessages, turnsToLog } from "@/shared/lib/chat-messages";

describe("parseChatLog", () => {
  it("returns empty for blank log", () => {
    expect(parseChatLog("")).toEqual([]);
    expect(parseChatLog("   ")).toEqual([]);
  });

  it("parses user and assistant turns", () => {
    const log = "> Hello\nHi there\n---\n> Second\nReply";
    expect(parseChatLog(log)).toEqual([
      { id: "user-0", role: "user", content: "Hello" },
      { id: "assistant-1", role: "assistant", content: "Hi there" },
      { id: "user-2", role: "user", content: "Second" },
      { id: "assistant-3", role: "assistant", content: "Reply" },
    ]);
  });

  it("parses streaming assistant without separator", () => {
    const log = "> Ping\nPartial stream";
    expect(parseChatLog(log)).toEqual([
      { id: "user-0", role: "user", content: "Ping" },
      { id: "assistant-1", role: "assistant", content: "Partial stream" },
    ]);
  });

  it("round-trips turns through turnsToLog", () => {
    const log = turnsToLog([
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello" },
    ]);
    expect(parseChatLog(log)).toEqual([
      { id: "user-0", role: "user", content: "Hi" },
      { id: "assistant-1", role: "assistant", content: "Hello" },
    ]);
  });

  it("parses trailing errors", () => {
    const log = "> Oops\npartial\n[error] harness down";
    expect(parseChatLog(log)).toEqual([
      { id: "user-0", role: "user", content: "Oops" },
      { id: "assistant-1", role: "assistant", content: "partial" },
      { id: "error-2", role: "error", content: "harness down" },
    ]);
  });
});

describe("turnsToInitialMessages", () => {
  it("maps user and assistant turns to UIMessage parts", () => {
    expect(
      turnsToInitialMessages([
        { role: "user", content: "Hi" },
        { role: "assistant", content: "Hello" },
      ]),
    ).toEqual([
      { id: "turn-0", role: "user", parts: [{ type: "text", content: "Hi" }] },
      { id: "turn-1", role: "assistant", parts: [{ type: "text", content: "Hello" }] },
    ]);
  });

  it("maps error turns for hydration", () => {
    expect(
      turnsToInitialMessages([
        { role: "user", content: "Oops" },
        { role: "error", content: "harness down" },
      ]),
    ).toEqual([
      { id: "turn-0", role: "user", parts: [{ type: "text", content: "Oops" }] },
      {
        id: "error-1",
        role: "assistant",
        parts: [{ type: "text", content: "harness down" }],
      },
    ]);
  });
});
