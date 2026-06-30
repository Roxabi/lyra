import { describe, expect, it, vi } from "vitest";
import { newTab, randomId } from "@/lib/chats-storage";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("randomId", () => {
  it("uses crypto.randomUUID when available", () => {
    const spy = vi
      .spyOn(crypto, "randomUUID")
      .mockReturnValue("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
    expect(randomId()).toBe("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
    spy.mockRestore();
  });

  it("falls back when crypto.randomUUID is unavailable (plain HTTP)", () => {
    const native = crypto.randomUUID;
    // @ts-expect-error — simulate non-secure context
    crypto.randomUUID = undefined;
    try {
      const id = randomId();
      expect(id).toMatch(UUID_RE);
      expect(randomId()).not.toBe(id);
    } finally {
      crypto.randomUUID = native;
    }
  });
});

describe("newTab", () => {
  it("uses DB defaults when provided", () => {
    const tab = newTab("lyra", { backend: "omp-rpc", model: "grok-4-fast" });
    expect(tab.harness).toBe("omp-rpc");
    expect(tab.model).toBe("grok-4-fast");
  });

  it("falls back to claude-cli/sonnet without defaults", () => {
    const tab = newTab("lyra");
    expect(tab.harness).toBe("claude-cli");
    expect(tab.model).toBe("sonnet");
  });

  it("accepts an initial session id for resumed sessions", () => {
    const tab = newTab("lyra", undefined, "sess-abc");
    expect(tab.sessionId).toBe("sess-abc");
  });
});
