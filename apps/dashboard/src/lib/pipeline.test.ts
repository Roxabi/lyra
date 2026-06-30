import { describe, expect, it } from "vitest";
import type { PipelineRun } from "@/lib/api";
import { filterPipelineRuns, isPipelineRowStale, matchesPipelineFilter } from "@/lib/pipeline";

function row(overrides: Partial<PipelineRun> = {}): PipelineRun {
  return {
    repo: "Roxabi/roxabi-factory",
    pr_number: 1,
    title: "test",
    head_sha: null,
    head_ref: null,
    html_url: null,
    reviewed: false,
    open: true,
    ci_status: "unknown",
    merge_status: "pending",
    publish_status: "n/a",
    m1_deploy_status: "n/a",
    cf_deploy_status: "n/a",
    checks: [],
    last_event_at: null,
    updated_at: null,
    ...overrides,
  };
}

describe("isPipelineRowStale", () => {
  it("marks rows older than 30 minutes as stale", () => {
    const now = Date.parse("2026-06-30T13:00:00.000Z");
    expect(isPipelineRowStale("2026-06-30T12:00:00.000Z", now)).toBe(true);
    expect(isPipelineRowStale("2026-06-30T12:45:00.000Z", now)).toBe(false);
  });
});

describe("matchesPipelineFilter", () => {
  it("filters CI failures", () => {
    expect(matchesPipelineFilter(row({ ci_status: "failure" }), "ci_red")).toBe(true);
    expect(matchesPipelineFilter(row({ ci_status: "success" }), "ci_red")).toBe(false);
  });

  it("filters awaiting reviewed", () => {
    expect(matchesPipelineFilter(row({ open: true, reviewed: false }), "awaiting_reviewed")).toBe(
      true,
    );
    expect(matchesPipelineFilter(row({ open: true, reviewed: true }), "awaiting_reviewed")).toBe(
      false,
    );
  });

  it("filters deploy pending", () => {
    expect(
      matchesPipelineFilter(
        row({
          open: false,
          merge_status: "success",
          publish_status: "pending",
        }),
        "deploy_pending",
      ),
    ).toBe(true);
  });
});

describe("filterPipelineRuns", () => {
  it("applies all active filters", () => {
    const runs = [
      row({ pr_number: 1, ci_status: "failure", reviewed: false }),
      row({ pr_number: 2, ci_status: "success", reviewed: false }),
    ];
    const filtered = filterPipelineRuns(runs, new Set(["ci_red", "awaiting_reviewed"]));
    expect(filtered.map((r) => r.pr_number)).toEqual([1]);
  });
});
