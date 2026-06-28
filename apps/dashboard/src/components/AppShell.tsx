import type { ReactNode } from "react";
import { AppNav } from "@/components/layout/AppNav";
import { ThemeToggle } from "@/components/ThemeToggle";

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="ember-canvas relative flex min-h-screen flex-col bg-background text-foreground">
      <header className="sticky top-0 z-50 bg-background/90 backdrop-blur-md">
        <div className="flex h-[var(--header-h)] items-center justify-between px-5">
          <div className="flex items-center gap-3">
            <img src="/factory-mark.svg" alt="" className="size-8" aria-hidden />
            <div className="flex flex-col">
              <span className="font-[family-name:var(--font-head)] text-sm font-bold tracking-tight">
                Roxabi Factory
              </span>
              <span className="text-xs text-muted-foreground">Console opérateur</span>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        <AppNav />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
      </div>
    </div>
  );
}
