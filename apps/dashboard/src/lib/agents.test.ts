import { describe, expect, it } from "vitest";
import { displayAgentName, normalizeAgentId } from "@/lib/agents";

describe("agents", () => {
  it("strips _default and title-cases", () => {
    expect(displayAgentName("lyra_default")).toBe("Lyra");
    expect(displayAgentName("aryl_default")).toBe("Aryl");
    expect(displayAgentName("lyra")).toBe("Lyra");
  });

  it("normalizes ids for API", () => {
    expect(normalizeAgentId("Lyra_Default")).toBe("lyra");
    expect(normalizeAgentId("aryl")).toBe("aryl");
  });
});
