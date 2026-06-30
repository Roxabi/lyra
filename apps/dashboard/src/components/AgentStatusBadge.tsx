import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { AgentHealth } from "@/lib/api";

interface AgentStatusBadgeProps {
  health: AgentHealth | undefined;
}

export function AgentStatusBadge({ health }: AgentStatusBadgeProps) {
  const { t } = useTranslation("common");
  if (!health) return null;
  if (health.online) {
    return <Badge variant="success">{t("status.online")}</Badge>;
  }
  return <Badge variant="destructive">{t("status.offline")}</Badge>;
}