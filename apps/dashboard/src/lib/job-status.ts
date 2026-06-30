export type JobStatusBadgeVariant = "success" | "secondary" | "destructive";

export function jobStatusToBadgeVariant(status: string): JobStatusBadgeVariant {
  if (status === "open") return "success";
  if (status === "closing") return "secondary";
  return "destructive";
}