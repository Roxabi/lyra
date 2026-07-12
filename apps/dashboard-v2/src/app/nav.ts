import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Bot,
  Briefcase,
  Container,
  GitPullRequest,
  LayoutDashboard,
  LineChart,
  Link2,
  MessageCircle,
  Palette,
  Plug,
  Users,
} from "lucide-react";

export type NavItemStatus = "ready" | "planned";

export interface AppNavItem {
  to: string;
  labelKey: string;
  Icon: LucideIcon;
  exact?: boolean;
  fullBleed?: boolean;
  wide?: boolean;
  status: NavItemStatus;
}

export const homeNavItems: AppNavItem[] = [
  {
    to: "/",
    labelKey: "nav.overview",
    Icon: LayoutDashboard,
    exact: true,
    wide: true,
    status: "ready",
  },
];

export const operateNavItems: AppNavItem[] = [
  {
    to: "/chat",
    labelKey: "nav.chat",
    Icon: MessageCircle,
    fullBleed: true,
    status: "ready",
  },
  { to: "/agents", labelKey: "nav.agents", Icon: Bot, wide: true, status: "ready" },
  { to: "/jobs", labelKey: "nav.jobs", Icon: Briefcase, wide: true, status: "ready" },
  {
    to: "/integrations",
    labelKey: "nav.integrations",
    Icon: Plug,
    wide: true,
    status: "ready",
  },
];

export const observeNavItems: AppNavItem[] = [
  {
    to: "/pipeline",
    labelKey: "nav.pipeline",
    Icon: GitPullRequest,
    wide: true,
    status: "ready",
  },
  { to: "/fleet", labelKey: "nav.fleet", Icon: Container, wide: true, status: "ready" },
  { to: "/ops", labelKey: "nav.ops", Icon: LineChart, wide: true, status: "ready" },
  { to: "/spans", labelKey: "nav.spans", Icon: Activity, wide: true, status: "ready" },
];

export const adminNavItems: AppNavItem[] = [
  {
    to: "/account/links",
    labelKey: "nav.accountLinks",
    Icon: Link2,
    wide: true,
    status: "ready",
  },
  {
    to: "/design-system",
    labelKey: "nav.designSystem",
    Icon: Palette,
    wide: true,
    status: "ready",
  },
  { to: "/users", labelKey: "nav.users", Icon: Users, wide: true, status: "ready" },
];

export const appNavItems: AppNavItem[] = [
  ...homeNavItems,
  ...operateNavItems,
  ...observeNavItems,
  ...adminNavItems,
];

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

export function resolveLayoutFlags(
  pathname: string,
  items: AppNavItem[] = appNavItems,
): { fullBleed: boolean; wide: boolean } {
  const sorted = [...items].sort((a, b) => b.to.length - a.to.length);
  const match = sorted.find((item) => isNavItemActive(pathname, item));
  return {
    fullBleed: match?.fullBleed ?? false,
    wide: match?.wide ?? false,
  };
}

/** @deprecated Use resolvePageTitle + i18n in components */
export function resolvePageLabel(pathname: string): string {
  const { key } = resolvePageTitle(pathname);
  return key;
}
