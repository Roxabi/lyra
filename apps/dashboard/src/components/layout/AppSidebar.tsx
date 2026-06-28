import { CaretDoubleLeft, CaretDoubleRight } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { AppNavLink } from "@/components/layout/AppNavLink";
import { ShellFooter } from "@/components/layout/ShellFooter";
import { Button } from "@/components/ui/button";
import { homeNavItems, observeNavItems, operateNavItems } from "@/lib/nav";
import { useSidebarCollapsed } from "@/lib/use-sidebar-collapsed";
import { cn } from "@/lib/utils";

function NavSection({ label, collapsed }: { label: string; collapsed: boolean }) {
  if (collapsed) return null;
  return (
    <p className="px-3 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {label}
    </p>
  );
}

export function AppSidebar() {
  const { t } = useTranslation();
  const { collapsed, toggle } = useSidebarCollapsed();

  return (
    <aside
      aria-label={t("appName")}
      className={cn(
        "hidden h-full shrink-0 flex-col bg-card transition-[width] duration-200 md:flex",
        collapsed ? "w-16" : "w-56 lg:w-60",
      )}
    >
      <div className={cn("flex items-center px-3 py-4", collapsed ? "justify-center" : "gap-2")}>
        {!collapsed ? (
          <div className="flex min-w-0 flex-1 items-center gap-2.5">
            <img src="/factory-mark.svg" alt="" className="size-8 shrink-0" aria-hidden />
            <div className="min-w-0">
              <p className="truncate font-[family-name:var(--font-head)] text-sm font-bold tracking-tight">
                {t("appName")}
              </p>
              <p className="truncate text-xs text-muted-foreground">{t("appTagline")}</p>
            </div>
          </div>
        ) : (
          <img src="/factory-mark.svg" alt="" className="size-8" aria-hidden />
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="shrink-0"
          onClick={toggle}
          aria-label={collapsed ? t("nav.expandSidebar") : t("nav.collapseSidebar")}
        >
          {collapsed ? (
            <CaretDoubleRight className="size-4" aria-hidden />
          ) : (
            <CaretDoubleLeft className="size-4" aria-hidden />
          )}
        </Button>
      </div>

      <nav
        className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto p-2 fd-scroll"
        aria-label={t("appName")}
      >
        <NavSection label={t("nav.sectionHome")} collapsed={collapsed} />
        {homeNavItems.map((item) => (
          <AppNavLink key={item.to} item={item} collapsed={collapsed} />
        ))}

        <NavSection label={t("nav.sectionOperate")} collapsed={collapsed} />
        {operateNavItems.map((item) => (
          <AppNavLink key={item.to} item={item} collapsed={collapsed} />
        ))}

        <NavSection label={t("nav.sectionObserve")} collapsed={collapsed} />
        {observeNavItems.map((item) => (
          <AppNavLink key={item.to} item={item} collapsed={collapsed} />
        ))}
      </nav>

      <ShellFooter collapsed={collapsed} />
    </aside>
  );
}
