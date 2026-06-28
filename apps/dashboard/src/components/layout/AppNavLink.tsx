import { Link, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { type AppNavItem, isNavItemActive } from "@/lib/nav";
import { cn } from "@/lib/utils";

interface AppNavLinkProps {
  item: AppNavItem;
  collapsed?: boolean;
}

export function AppNavLink({ item, collapsed = false }: AppNavLinkProps) {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const active = isNavItemActive(pathname, item);
  const Icon = item.Icon;
  const label = t(item.labelKey);

  return (
    <Link
      to={item.to}
      title={collapsed ? label : undefined}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center rounded-lg text-sm transition-colors",
        collapsed ? "justify-center px-2 py-2.5" : "gap-3 px-3 py-2",
        active
          ? "bg-brand/15 font-medium text-foreground"
          : "font-medium text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      <Icon className="size-4 shrink-0" weight={active ? "fill" : "regular"} aria-hidden />
      {!collapsed ? <span className="truncate">{label}</span> : null}
    </Link>
  );
}
