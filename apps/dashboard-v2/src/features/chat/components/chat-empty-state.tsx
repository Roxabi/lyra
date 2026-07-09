import { MessageCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ChatEmptyStateProps {
  agent: string;
  offline: boolean;
}

export function ChatEmptyState({ agent, offline }: ChatEmptyStateProps) {
  const { t } = useTranslation("chat");
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      <div className="flex size-14 items-center justify-center rounded-2xl border bg-card shadow-sm">
        <MessageCircle className="size-7 text-muted-foreground" aria-hidden />
      </div>
      <div className="max-w-sm space-y-1.5">
        <p className="text-base font-semibold text-foreground">
          {t("emptyState.title", { agent })}
        </p>
        <p className="text-sm text-muted-foreground">
          {offline ? t("emptyState.offline") : t("emptyState.online")}
        </p>
      </div>
    </div>
  );
}
