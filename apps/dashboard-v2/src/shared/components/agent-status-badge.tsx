import { Badge } from "@/components/ui/badge";
import type { AgentHealth } from "@/shared/api/bff-types";

interface AgentStatusBadgeProps {
  health: AgentHealth | undefined;
}

export function AgentStatusBadge({ health }: AgentStatusBadgeProps) {
  if (!health) return null;
  if (health.online) {
    return <Badge variant="default">Online</Badge>;
  }
  return <Badge variant="destructive">Offline</Badge>;
}
