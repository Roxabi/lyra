import { Badge } from "@/components/ui/badge";
import type { AgentHealth } from "@/lib/api";

interface AgentStatusBadgeProps {
  health: AgentHealth | undefined;
}

export function AgentStatusBadge({ health }: AgentStatusBadgeProps) {
  if (!health) return null;
  if (health.online) {
    return <Badge variant="success">En ligne</Badge>;
  }
  return <Badge variant="destructive">Hors ligne</Badge>;
}
