import { useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { resolvePageTitle } from "@/app/nav";
import { useShellTitle } from "@/app/shell-title";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

/**
 * Slim top bar — page title + sidebar trigger.
 * Account chrome lives in sidebar footer (NavUser).
 */
export function AppHeader() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { override } = useShellTitle();
  const { t } = useTranslation("common");
  const { key } = resolvePageTitle(pathname);
  const label = override.literal ?? t(key);

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
      <SidebarTrigger />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <span className="min-w-0 truncate font-heading text-lg font-semibold tracking-tight">
        {label}
      </span>
    </header>
  );
}
