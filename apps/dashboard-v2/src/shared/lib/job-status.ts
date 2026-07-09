export type JobStatusBadgeVariant = "default" | "secondary" | "destructive";

/** @deprecated Prefer JobStatusBadge — uses Forge status tokens, not primary orange. */
export function jobStatusToBadgeVariant(status: string): JobStatusBadgeVariant {
  if (status === "open") return "default";
  if (status === "closing") return "secondary";
  return "destructive";
}

/** Tailwind classes backed by shadcn @theme status-* tokens (forge.css). */
export function jobStatusClass(status: string): string {
  if (status === "open") {
    return "border-status-open/30 bg-status-open/15 text-status-open";
  }
  if (status === "closing") {
    return "border-status-closing/30 bg-status-closing/15 text-status-closing";
  }
  return "border-status-error/30 bg-status-error/15 text-status-error";
}
