import { useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { resolvePageTitle } from "@/lib/nav";
import { useShellTitleContext } from "@/lib/shell-title";

export function AppHeader() {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { override } = useShellTitleContext();
  const title = resolvePageTitle(pathname);
  const label = override.literal ?? t(title.key);

  return (
    <header className="sticky top-0 z-10 shrink-0 border-b bg-card/80 px-4 py-3 backdrop-blur md:px-6">
      <h1 className="truncate font-[family-name:var(--font-head)] text-lg font-semibold tracking-tight">
        {label}
      </h1>
    </header>
  );
}