import { describe, expect, it } from "vitest";
import { jobStatusClass, jobStatusToBadgeVariant } from "@/shared/lib/job-status";

describe("jobStatusToBadgeVariant", () => {
  it("maps open → default", () => {
    expect(jobStatusToBadgeVariant("open")).toBe("default");
  });

  it("maps closing → secondary", () => {
    expect(jobStatusToBadgeVariant("closing")).toBe("secondary");
  });

  it("maps any other status → destructive", () => {
    expect(jobStatusToBadgeVariant("failed")).toBe("destructive");
    expect(jobStatusToBadgeVariant("")).toBe("destructive");
  });
});

describe("jobStatusClass", () => {
  it("uses status-open token for open jobs", () => {
    expect(jobStatusClass("open")).toContain("status-open");
  });

  it("uses status-closing token for closing jobs", () => {
    expect(jobStatusClass("closing")).toContain("status-closing");
  });

  it("uses status-error token for other statuses", () => {
    expect(jobStatusClass("failed")).toContain("status-error");
  });
});
