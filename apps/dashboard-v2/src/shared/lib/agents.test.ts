import { describe, expect, it } from "vitest";
import { displayAgentName, normalizeAgentId } from "@/shared/lib/agents";

describe("agents", () => {
  it("displayAgentName strips _default and title-cases", () => {
    expect(displayAgentName("lyra_default")).toBe("Lyra");
    expect(displayAgentName("aryl")).toBe("Aryl");
  });

  it("normalizeAgentId lowercases and strips _default", () => {
    expect(normalizeAgentId("Lyra_default")).toBe("lyra");
    expect(normalizeAgentId("ARYL")).toBe("aryl");
  });
});
