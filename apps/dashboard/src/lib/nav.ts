import {
  Briefcase,
  ChartLineUp,
  ChatCircleDots,
  type Icon,
  Palette,
  Plugs,
  Robot,
  ShippingContainer,
  SquaresFour,
  Users,
} from "@phosphor-icons/react";

export interface AppNavItem {
  to: string;
  labelKey: string;
  Icon: Icon;
  exact?: boolean;
  fullBleed?: boolean;
  wideLayout?: boolean;
  hideBottomNav?: boolean;
  mobileBottomNav?: boolean;
}

export const homeNavItems: AppNavItem[] = [
  {
    to: "/",
    labelKey: "nav.overview",
    Icon: SquaresFour,
    exact: true,
    wideLayout: true,
    mobileBottomNav: true,
  },
];

export const operateNavItems: AppNavItem[] = [
  {
    to: "/chat",
    labelKey: "nav.chat",
    Icon: ChatCircleDots,
    fullBleed: true,
    hideBottomNav: true,
    mobileBottomNav: true,
  },
  { to: "/agents", labelKey: "nav.agents", Icon: Robot, wideLayout: true, mobileBottomNav: true },
  { to: "/jobs", labelKey: "nav.jobs", Icon: Briefcase, wideLayout: true },
  { to: "/integrations", labelKey: "nav.integrations", Icon: Plugs, wideLayout: true },
];

export const observeNavItems: AppNavItem[] = [
  { to: "/fleet", labelKey: "nav.fleet", Icon: ShippingContainer, wideLayout: true },
  { to: "/ops", labelKey: "nav.ops", Icon: ChartLineUp, wideLayout: true, mobileBottomNav: true },
];

export const adminNavItems: AppNavItem[] = [
  { to: "/design-system", labelKey: "nav.designSystem", Icon: Palette, wideLayout: true },
  { to: "/users", labelKey: "nav.users", Icon: Users, wideLayout: true },
];

export const appNavItems: AppNavItem[] = [
  ...homeNavItems,
  ...operateNavItems,
  ...observeNavItems,
  ...adminNavItems,
];

export const mobileBottomNavItems: AppNavItem[] = appNavItems.filter(
  (item) => item.mobileBottomNav,
);

export interface PageTitleDescriptor {
  key: string;
}

export function isNavItemActive(pathname: string, item: AppNavItem): boolean {
  if (item.exact) return pathname === item.to;
  return pathname === item.to || pathname.startsWith(`${item.to}/`);
}

export function resolvePageTitle(pathname: string): PageTitleDescriptor {
  const sorted = [...appNavItems].sort((a, b) => b.to.length - a.to.length);
  const match = sorted.find((item) => isNavItemActive(pathname, item));
  if (match) return { key: match.labelKey };
  return { key: "appName" };
}

export function resolveNavFlags(
  pathname: string,
  items: AppNavItem[] = appNavItems,
): { fullBleed: boolean; wideLayout: boolean; hideBottomNav: boolean } {
  const sorted = [...items].sort((a, b) => b.to.length - a.to.length);
  const match = sorted.find((item) => isNavItemActive(pathname, item));
  return {
    fullBleed: match?.fullBleed ?? false,
    wideLayout: match?.wideLayout ?? false,
    hideBottomNav: match?.hideBottomNav ?? false,
  };
}
