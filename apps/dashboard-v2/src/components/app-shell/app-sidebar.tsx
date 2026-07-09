import { Link, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  type AppNavItem,
  adminNavItems,
  homeNavItems,
  isNavItemActive,
  observeNavItems,
  operateNavItems,
} from "@/app/nav";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

function NavSection({ titleKey, items }: { titleKey: string; items: AppNavItem[] }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { t } = useTranslation("common");

  return (
    <SidebarGroup>
      <SidebarGroupLabel>{t(titleKey)}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => {
            const active = isNavItemActive(pathname, item);
            const Icon = item.Icon;
            const label = t(item.labelKey);

            if (item.status === "planned") {
              return (
                <SidebarMenuItem key={item.to}>
                  <SidebarMenuButton
                    isActive={false}
                    disabled
                    tooltip={t("nav.comingSoon", { label })}
                    className="opacity-50"
                  >
                    <Icon />
                    <span>{label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            }

            return (
              <SidebarMenuItem key={item.to}>
                <SidebarMenuButton isActive={active} tooltip={label} render={<Link to={item.to} />}>
                  <Icon />
                  <span>{label}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

export function AppSidebar() {
  const { t } = useTranslation("common");

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="border-b border-sidebar-border">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<Link to="/" />}>
              <img src="/factory-mark.svg" alt="" className="size-8" aria-hidden />
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">{t("appName")}</span>
                <span className="truncate text-xs text-muted-foreground">{t("appTagline")}</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <NavSection titleKey="nav.sectionHome" items={homeNavItems} />
        <NavSection titleKey="nav.sectionOperate" items={operateNavItems} />
        <NavSection titleKey="nav.sectionObserve" items={observeNavItems} />
        <NavSection titleKey="nav.sectionAdmin" items={adminNavItems} />
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border p-2">
        <span className="px-2 font-mono text-[10px] font-bold uppercase tracking-widest text-muted-foreground group-data-[collapsible=icon]:hidden">
          {t("nav.badgeV2")}
        </span>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
