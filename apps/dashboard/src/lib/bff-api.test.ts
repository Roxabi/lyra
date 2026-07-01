import { describe, expect, it } from "vitest";
import { type BffApiError, classifyBffDetail, parseBffResponse } from "@/lib/bff-api";

describe("classifyBffDetail", () => {
  it("detects email conflicts", () => {
    expect(classifyBffDetail("email already registered: ops@example.com")).toBe("email_conflict");
  });

  it("detects platform identity conflicts", () => {
    expect(classifyBffDetail("platform identity already linked: tg:user:1")).toBe(
      "platform_conflict",
    );
  });

  it("detects unknown agents", () => {
    expect(classifyBffDetail("unknown agent(s): missing")).toBe("unknown_agent");
  });

  it("detects agent name conflicts", () => {
    expect(classifyBffDetail("agent 'lyra' already exists")).toBe("agent_conflict");
  });

  it("detects not_found and generic fallbacks", () => {
    expect(classifyBffDetail("user not_found")).toBe("not_found");
    expect(classifyBffDetail("")).toBe("generic");
    expect(classifyBffDetail("something else")).toBe("generic");
  });
});

describe("parseBffResponse", () => {
  it("returns JSON on success", async () => {
    const res = new Response(JSON.stringify({ ok: true }), { status: 200 });
    await expect(parseBffResponse<{ ok: boolean }>(res)).resolves.toEqual({ ok: true });
  });

  it("throws BffApiError with detail on conflict", async () => {
    const res = new Response(JSON.stringify({ detail: "email already registered" }), {
      status: 409,
    });
    await expect(parseBffResponse(res)).rejects.toMatchObject({
      status: 409,
      detail: "email already registered",
      code: "email_conflict",
    } satisfies Partial<BffApiError>);
  });
});
