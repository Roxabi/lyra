import { describe, expect, it } from "vitest";
import type { DashboardJob } from "@/lib/api";
import { filterJobs, sortJobs } from "@/lib/jobs-filters";

const JOBS: DashboardJob[] = [
  {
    job_id: "job-b",
    pool_id: "pool-1",
    agent: "lyra",
    platform: "omp",
    status: "running",
    started_at: "2026-06-30T10:00:00Z",
    concurrency_mode: "exclusive",
    worker_loc: null,
    steer_subject: "steer.b",
  },
  {
    job_id: "job-a",
    pool_id: "pool-2",
    agent: "aryl",
    platform: "omp",
    status: "queued",
    started_at: "2026-06-30T09:00:00Z",
    concurrency_mode: "shared",
    worker_loc: null,
    steer_subject: "steer.a",
  },
];

describe("jobs-filters", () => {
  it("filters by status and search", () => {
    expect(filterJobs(JOBS, { search: "lyra", statuses: [] })).toHaveLength(1);
    expect(filterJobs(JOBS, { search: "", statuses: ["queued"] })).toHaveLength(1);
    expect(filterJobs(JOBS, { search: "missing", statuses: [] })).toHaveLength(0);
  });

  it("sorts by job id ascending", () => {
    const sorted = sortJobs(JOBS, "job_id", "asc");
    expect(sorted.map((j) => j.job_id)).toEqual(["job-a", "job-b"]);
  });
});