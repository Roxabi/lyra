import { Briefcase, ChartLineUp, ChatCircleDots, SquaresFour } from "@phosphor-icons/react";
import { Link, useRouterState } from "@tanstack/react-router";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Dashboard", icon: SquaresFour, match: (p: string) => p === "/" },
  {
    to: "/chat",
    label: "Chat",
    icon: ChatCircleDots,
    match: (p: string) => p.startsWith("/chat"),
  },
  {
    to: "/jobs",
    label: "Jobs",
    icon: Briefcase,
    match: (p: string) => p.startsWith("/jobs"),
  },
  { to: "/ops", label: "Ops", icon: ChartLineUp, match: (p: string) => p.startsWith("/ops") },
] as const;

export function AppNav() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <nav className="flex w-56 shrink-0 flex-col bg-card">
      <div className="px-4 py-4">
        <p className="font-[family-name:var(--font-head)] text-sm font-bold">Factory</p>
        <p className="text-xs text-muted-foreground">Control plane</p>
      </div>
      <ul className="flex flex-1 flex-col gap-0.5 px-2">
        {NAV.map((item) => {
          const active = item.match(pathname);
          const Icon = item.icon;
          return (
            <li key={item.to}>
              <Link
                to={item.to}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-colors",
                  active
                    ? "bg-primary/12 font-medium text-foreground"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                )}
              >
                <Icon className="size-4 shrink-0" weight={active ? "fill" : "regular"} />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
