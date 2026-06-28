import { describe, expect, it } from "vitest";
import { parseChatLog } from "@/lib/chat-messages";

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

  it("parses trailing errors", () => {
    const log = "> Oops\npartial\n[error] harness down";
    expect(parseChatLog(log)).toEqual([
      { id: "user-0", role: "user", content: "Oops" },
      { id: "assistant-1", role: "assistant", content: "partial" },
      { id: "error-2", role: "error", content: "harness down" },
    ]);
  });
});
