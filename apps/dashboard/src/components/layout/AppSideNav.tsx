import { Icon } from "@astryxdesign/core/Icon";
import {
  SideNav,
  SideNavCollapseButton,
  SideNavHeading,
  SideNavItem,
  SideNavSection,
} from "@astryxdesign/core/SideNav";
import { useRouterState } from "@tanstack/react-router";
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

export function AppSideNav() {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { collapsed, setCollapsed } = useSidebarCollapsed();

  const renderItems = (items: AppNavItem[]) =>
    items.map((item) => {
      const active = isNavItemActive(pathname, item);
      return (
        <SideNavItem
          key={item.to}
          label={t(item.labelKey)}
          href={item.to}
          isSelected={active}
          // Domain glyph routed through Astryx Icon (#2091). Active state is
          // conveyed by SideNavItem's isSelected highlight, so no weight swap.
          icon={<Icon icon={item.Icon} size="sm" />}
        />
      );
    });

  return (
    <SideNav
      // `hasButton: false` — Astryx auto-renders a collapse button with a
      // hardcoded English label; we render our own (footerIcons) with a
      // translated label to keep the FR/EN a11y coverage the old shell had.
      // `setCollapsed` persists the preference (see useSidebarCollapsed).
      collapsible={{ isCollapsed: collapsed, onCollapsedChange: setCollapsed, hasButton: false }}
      header={
        <SideNavHeading
          icon={<img src="/factory-mark.svg" alt="" className="size-8" aria-hidden />}
          heading={t("appName")}
          subheading={t("appTagline")}
          headingHref="/"
        />
      }
      footer={<UserMenu variant="sidebar" collapsed={collapsed} />}
      footerIcons={
        <SideNavCollapseButton label={t(collapsed ? "nav.expandSidebar" : "nav.collapseSidebar")} />
      }
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
