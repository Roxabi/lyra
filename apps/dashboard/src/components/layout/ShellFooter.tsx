import { useTranslation } from "react-i18next";
import { ThemeToggle } from "@/components/ThemeToggle";
import { cn } from "@/lib/utils";

interface ShellFooterProps {
  collapsed?: boolean;
}

export function ShellFooter({ collapsed = false }: ShellFooterProps) {
  const { t } = useTranslation();

  return (
    <div
      className={cn(
        "flex items-center border-t p-2",
        collapsed ? "justify-center" : "justify-between gap-2 px-3",
      )}
    >
      {!collapsed ? (
        <div className="min-w-0">
          <p className="truncate text-xs font-medium">{t("appName")}</p>
          <p className="truncate text-[10px] text-muted-foreground">{t("appTagline")}</p>
        </div>
      ) : null}
      <ThemeToggle variant={collapsed ? "icon" : "segment"} />
    </div>
  );
}
