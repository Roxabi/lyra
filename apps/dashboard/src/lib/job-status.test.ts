import { describe, expect, it } from "vitest";
import { jobStatusToBadgeVariant } from "@/lib/job-status";

describe("jobStatusToBadgeVariant", () => {
  it("maps open → success", () => {
    expect(jobStatusToBadgeVariant("open")).toBe("success");
  });

  it("maps closing → neutral", () => {
    expect(jobStatusToBadgeVariant("closing")).toBe("neutral");
  });

  it("maps any other status → error", () => {
    expect(jobStatusToBadgeVariant("failed")).toBe("error");
    expect(jobStatusToBadgeVariant("")).toBe("error");
  });
});
