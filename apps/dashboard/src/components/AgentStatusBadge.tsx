import { Badge } from "@astryxdesign/core/Badge";
import { useTranslation } from "react-i18next";
import type { AgentHealth } from "@/lib/api";

interface AgentStatusBadgeProps {
  health: AgentHealth | undefined;
}

export function AgentStatusBadge({ health }: AgentStatusBadgeProps) {
  const { t } = useTranslation("common");
  if (!health) return null;
  if (health.online) {
    return <Badge variant="success" label={t("status.online")} />;
  }
  return <Badge variant="error" label={t("status.offline")} />;
}
