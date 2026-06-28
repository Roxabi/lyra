import type { ReactNode } from "react";
import { ThemeToggle } from "@/components/ThemeToggle";

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="ember-canvas relative min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-50 border-b border-border bg-background/90 backdrop-blur-md">
        <div className="flex h-[var(--header-h)] items-center justify-between px-5">
          <div className="flex items-center gap-3">
            <img src="/factory-mark.svg" alt="" className="size-8" aria-hidden />
            <div className="flex flex-col">
              <span className="font-[family-name:var(--font-head)] text-sm font-bold tracking-tight text-foreground">
                Roxabi Factory
              </span>
              <span className="text-xs text-muted-foreground">Console opérateur</span>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>
      <main className="relative z-10">{children}</main>
    </div>
  );
}
