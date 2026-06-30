import { useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { AppNavLink } from "@/components/layout/AppNavLink";
import { appNavItems, mobileBottomNavItems, resolveNavFlags } from "@/lib/nav";

export function AppBottomNav() {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { hideBottomNav } = resolveNavFlags(pathname, appNavItems);

  if (hideBottomNav) return null;

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-20 border-t border-border bg-card pb-safe md:hidden"
      aria-label={t("nav.main")}
    >
      <div className="mx-auto flex h-14 w-full max-w-lg items-stretch">
        {mobileBottomNavItems.map((item) => (
          <AppNavLink key={item.to} item={item} variant="bottom" />
        ))}
      </div>
    </nav>
  );
}