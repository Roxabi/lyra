import { displayAgentName } from "@/lib/agents";
import type { DashboardJob } from "@/lib/api";
import { compareStrings, type SortDirection } from "@/lib/sort";

export type JobsSortKey = "job_id" | "agent" | "status" | "started_at";

export function filterJobs(
  jobs: DashboardJob[],
  { search, statuses }: { search: string; statuses: string[] },
): DashboardJob[] {
  const q = search.trim().toLowerCase();
  return jobs.filter((job) => {
    if (statuses.length > 0 && !statuses.includes(job.status)) return false;
    if (!q) return true;
    const haystack = [
      job.job_id,
      job.pool_id,
      job.agent ? displayAgentName(job.agent) : "",
      job.agent ?? "",
      job.status,
      job.platform ?? "",
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });
}

export function sortJobs(
  jobs: DashboardJob[],
  sortKey: JobsSortKey,
  direction: SortDirection,
): DashboardJob[] {
  const sorted = [...jobs];
  sorted.sort((a, b) => {
    switch (sortKey) {
      case "job_id":
        return compareStrings(a.job_id, b.job_id, direction);
      case "agent":
        return compareStrings(a.agent ?? "", b.agent ?? "", direction);
      case "status":
        return compareStrings(a.status, b.status, direction);
      case "started_at":
        return compareStrings(a.started_at, b.started_at, direction);
      default:
        return 0;
    }
  });
  return sorted;
}

export function uniqueJobStatuses(jobs: DashboardJob[]): string[] {
  return [...new Set(jobs.map((j) => j.status))].sort();
}