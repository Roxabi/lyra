import type { AgentHealth } from "@/lib/api";

interface AgentStatusBadgeProps {
  health: AgentHealth | undefined;
}

export function AgentStatusBadge({ health }: AgentStatusBadgeProps) {
  if (!health) return null;
  if (health.online) {
    return (
      <span className="rounded bg-status-open/20 px-2 py-0.5 text-[10px] font-medium text-status-open">
        OK
      </span>
    );
  }
  return (
    <span className="rounded bg-destructive/20 px-2 py-0.5 text-[10px] font-medium text-destructive">
      Hors ligne
    </span>
  );
}
