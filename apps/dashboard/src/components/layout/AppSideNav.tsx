import { SideNav, SideNavHeading, SideNavItem, SideNavSection } from "@astryxdesign/core/SideNav";
import { useRouterState } from "@tanstack/react-router";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { UserMenu } from "@/components/UserMenu";
import {
  type AppNavItem,
  adminNavItems,
  homeNavItems,
  isNavItemActive,
  observeNavItems,
  operateNavItems,
} from "@/lib/nav";
import { useSidebarCollapsed } from "@/lib/use-sidebar-collapsed";

// Mirrors the key `useSidebarCollapsed`'s own `toggle()` persists to. Astryx's
// `collapsible.onCollapsedChange` bypasses that helper, so this component
// persists the value itself on every change to avoid losing the preference.
const SIDEBAR_STORAGE_KEY = "factory.dashboard.sidebar-collapsed";

export function AppSideNav() {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { collapsed, setCollapsed } = useSidebarCollapsed();

  const handleCollapsedChange = useCallback(
    (next: boolean) => {
      setCollapsed(next);
      try {
        localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? "1" : "0");
      } catch {
        // ignore — persistence is best-effort
      }
    },
    [setCollapsed],
  );

  const renderItems = (items: AppNavItem[]) =>
    items.map((item) => {
      const active = isNavItemActive(pathname, item);
      const Icon = item.Icon;
      return (
        <SideNavItem
          key={item.to}
          label={t(item.labelKey)}
          href={item.to}
          isSelected={active}
          icon={<Icon className="size-4" weight={active ? "fill" : "regular"} aria-hidden />}
        />
      );
    });

  return (
    <SideNav
      collapsible={{ isCollapsed: collapsed, onCollapsedChange: handleCollapsedChange }}
      header={
        <SideNavHeading
          icon={<img src="/factory-mark.svg" alt="" className="size-8" aria-hidden />}
          heading={t("appName")}
          subheading={t("appTagline")}
          headingHref="/"
        />
      }
      footer={<UserMenu variant="sidebar" collapsed={collapsed} />}
    >
      <SideNavSection title={t("nav.sectionHome")}>{renderItems(homeNavItems)}</SideNavSection>
      <SideNavSection title={t("nav.sectionOperate")}>
        {renderItems(operateNavItems)}
      </SideNavSection>
      <SideNavSection title={t("nav.sectionObserve")}>
        {renderItems(observeNavItems)}
      </SideNavSection>
      <SideNavSection title={t("nav.sectionAdmin")}>{renderItems(adminNavItems)}</SideNavSection>
    </SideNav>
  );
}
