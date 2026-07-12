import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";

export function ChatReadyBadge({ ready }: { ready: boolean }) {
  const { t } = useTranslation("auth");
  return (
    <Badge variant={ready ? "default" : "outline"}>
      {ready ? t("links.chatReady") : t("links.chatNotReady")}
    </Badge>
  );
}
