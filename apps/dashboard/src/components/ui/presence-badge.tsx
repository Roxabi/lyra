import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";

interface PresenceBadgeProps {
  present: boolean;
  namespace?: "admin" | "agents";
}

export function PresenceBadge({ present, namespace = "admin" }: PresenceBadgeProps) {
  const { t } = useTranslation(namespace);
  return (
    <Badge variant={present ? "success" : "secondary"} className="text-[10px]">
      {present ? t("platformLinked") : t("platformNotLinked")}
    </Badge>
  );
}
