import { Badge } from "@astryxdesign/core/Badge";
import { useTranslation } from "react-i18next";

interface PresenceBadgeProps {
  present: boolean;
  namespace?: "admin" | "agents";
}

export function PresenceBadge({ present, namespace = "admin" }: PresenceBadgeProps) {
  const { t } = useTranslation(namespace);
  return (
    <Badge
      variant={present ? "success" : "neutral"}
      className="text-[10px]"
      label={present ? t("platformLinked") : t("platformNotLinked")}
    />
  );
}
