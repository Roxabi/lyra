import type { PipelineRun } from "@/lib/api";

export const PIPELINE_STALE_MS = 30 * 60 * 1000;

export type PipelineFilter = "ci_red" | "awaiting_reviewed" | "deploy_pending";

export function isPipelineRowStale(
  lastEventAt: string | null,
  nowMs: number = Date.now(),
): boolean {
  if (!lastEventAt) return true;
  const ts = Date.parse(lastEventAt);
  if (Number.isNaN(ts)) return true;
  return nowMs - ts > PIPELINE_STALE_MS;
}

export function matchesPipelineFilter(row: PipelineRun, filter: PipelineFilter): boolean {
  switch (filter) {
    case "ci_red":
      return row.ci_status === "failure";
    case "awaiting_reviewed":
      return row.open && !row.reviewed;
    case "deploy_pending":
      return (
        !row.open &&
        row.merge_status === "success" &&
        (row.publish_status === "pending" ||
          row.m1_deploy_status === "pending" ||
          row.cf_deploy_status === "pending")
      );
    default:
      return true;
  }
}

export function filterPipelineRuns(
  runs: PipelineRun[],
  active: Set<PipelineFilter>,
): PipelineRun[] {
  if (active.size === 0) return runs;
  return runs.filter((row) => [...active].every((filter) => matchesPipelineFilter(row, filter)));
}
