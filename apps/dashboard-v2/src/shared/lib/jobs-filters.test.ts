import { describe, expect, it } from "vitest";
import type { DashboardJob } from "@/shared/api/bff-types";
import { filterJobs, sortJobs, uniqueJobStatuses } from "@/shared/lib/jobs-filters";

const JOBS: DashboardJob[] = [
  {
    job_id: "job-b",
    pool_id: "pool-b",
    agent: "aryl",
    platform: "telegram",
    status: "closing",
    started_at: "2026-06-30T10:00:00Z",
    concurrency_mode: "queue",
    worker_loc: null,
    steer_subject: "steer-b",
  },
  {
    job_id: "job-a",
    pool_id: "pool-a",
    agent: "lyra",
    platform: "web",
    status: "open",
    started_at: "2026-06-30T11:00:00Z",
    concurrency_mode: "steer",
    worker_loc: "worker",
    steer_subject: "steer-a",
  },
];

describe("jobs-filters", () => {
  it("filters by search and status", () => {
    expect(filterJobs(JOBS, { search: "lyra", statuses: [] })).toHaveLength(1);
    expect(filterJobs(JOBS, { search: "", statuses: ["open"] })).toHaveLength(1);
  });

  it("sorts by job_id ascending", () => {
    const sorted = sortJobs(JOBS, "job_id", "asc");
    expect(sorted[0]?.job_id).toBe("job-a");
  });

  it("collects unique statuses", () => {
    expect(uniqueJobStatuses(JOBS)).toEqual(["closing", "open"]);
  });
});
