import { Link, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { type AppNavItem, isNavItemActive } from "@/lib/nav";
import { cn } from "@/lib/utils";

interface AppNavLinkProps {
  item: AppNavItem;
  variant?: "sidebar" | "bottom";
  collapsed?: boolean;
}

export function AppNavLink({ item, variant = "sidebar", collapsed = false }: AppNavLinkProps) {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const active = isNavItemActive(pathname, item);
  const Icon = item.Icon;
  const label = t(item.labelKey);

  if (variant === "bottom") {
    return (
      <Link
        to={item.to}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex min-h-10 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-1 text-[10px] transition-colors active:scale-[0.98]",
          active
            ? "font-medium text-foreground"
            : "font-medium text-muted-foreground hover:bg-accent hover:text-foreground",
        )}
      >
        <span
          className={cn(
            "flex size-6 items-center justify-center rounded-full transition-colors",
            active && "bg-brand/15",
          )}
        >
          <Icon className="size-5 shrink-0" weight={active ? "fill" : "regular"} aria-hidden />
        </span>
        {label}
      </Link>
    );
  }

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