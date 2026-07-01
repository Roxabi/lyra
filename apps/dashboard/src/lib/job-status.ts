import type { BadgeVariant } from "@astryxdesign/core/Badge";

export type JobStatusBadgeVariant = Extract<BadgeVariant, "success" | "neutral" | "error">;

export function jobStatusToBadgeVariant(status: string): JobStatusBadgeVariant {
  if (status === "open") return "success";
  if (status === "closing") return "neutral";
  return "error";
}
