import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Bot,
  Briefcase,
  Container,
  GitPullRequest,
  LayoutDashboard,
  LineChart,
  MessageCircle,
  Palette,
  Plug,
  Users,
} from "lucide-react";

export type NavItemStatus = "ready" | "planned";

export interface AppNavItem {
  to: string;
  label: string;
  Icon: LucideIcon;
  exact?: boolean;
  fullBleed?: boolean;
  wide?: boolean;
  status: NavItemStatus;
}

export const APP_NAME = "Factory";
export const APP_TAGLINE = "Operator dashboard";

export const homeNavItems: AppNavItem[] = [
  {
    to: "/",
    label: "Overview",
    Icon: LayoutDashboard,
    exact: true,
    wide: true,
    status: "ready",
  },
];

export const operateNavItems: AppNavItem[] = [
  { to: "/chat", label: "Chat", Icon: MessageCircle, fullBleed: true, status: "ready" },
  { to: "/agents", label: "Agents", Icon: Bot, wide: true, status: "ready" },
  { to: "/jobs", label: "Jobs", Icon: Briefcase, wide: true, status: "ready" },
  { to: "/integrations", label: "Integrations", Icon: Plug, wide: true, status: "ready" },
];

export const observeNavItems: AppNavItem[] = [
  { to: "/pipeline", label: "Pipeline", Icon: GitPullRequest, wide: true, status: "ready" },
  { to: "/fleet", label: "Fleet", Icon: Container, wide: true, status: "ready" },
  { to: "/ops", label: "Ops", Icon: LineChart, wide: true, status: "ready" },
  { to: "/spans", label: "Spans", Icon: Activity, wide: true, status: "ready" },
];

export const adminNavItems: AppNavItem[] = [
  {
    to: "/design-system",
    label: "Design system",
    Icon: Palette,
    wide: true,
    status: "ready",
  },
  { to: "/users", label: "Users", Icon: Users, wide: true, status: "ready" },
];

export const appNavItems: AppNavItem[] = [
  ...homeNavItems,
  ...operateNavItems,
  ...observeNavItems,
  ...adminNavItems,
];

export function isNavItemActive(pathname: string, item: AppNavItem): boolean {
  if (item.exact) return pathname === item.to;
  return pathname === item.to || pathname.startsWith(`${item.to}/`);
}

export function resolvePageLabel(pathname: string): string {
  const sorted = [...appNavItems].sort((a, b) => b.to.length - a.to.length);
  const match = sorted.find((item) => isNavItemActive(pathname, item));
  return match?.label ?? APP_NAME;
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
