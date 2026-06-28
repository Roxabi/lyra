import {
  Briefcase,
  ChartLineUp,
  ChatCircleDots,
  type Icon,
  SquaresFour,
} from "@phosphor-icons/react";

export interface AppNavItem {
  to: string;
  labelKey: string;
  Icon: Icon;
  exact?: boolean;
  fullBleed?: boolean;
  wideLayout?: boolean;
}

export const homeNavItems: AppNavItem[] = [
  { to: "/", labelKey: "nav.overview", Icon: SquaresFour, exact: true, wideLayout: true },
];

export const operateNavItems: AppNavItem[] = [
  { to: "/chat", labelKey: "nav.chat", Icon: ChatCircleDots, fullBleed: true },
  { to: "/jobs", labelKey: "nav.jobs", Icon: Briefcase, wideLayout: true },
];

export const observeNavItems: AppNavItem[] = [
  { to: "/ops", labelKey: "nav.ops", Icon: ChartLineUp, wideLayout: true },
];

export const appNavItems: AppNavItem[] = [...homeNavItems, ...operateNavItems, ...observeNavItems];

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
): { fullBleed: boolean; wideLayout: boolean } {
  const sorted = [...items].sort((a, b) => b.to.length - a.to.length);
  const match = sorted.find((item) => isNavItemActive(pathname, item));
  return {
    fullBleed: match?.fullBleed ?? false,
    wideLayout: match?.wideLayout ?? false,
  };
}
