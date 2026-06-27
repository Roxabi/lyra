import type { ReactNode } from "react";
import { ThemeToggle } from "@/components/ThemeToggle";

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="ember-canvas relative min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-50 border-b border-border bg-background/85 backdrop-blur-md">
        <div className="flex h-14 items-center justify-between px-4">
          <div className="flex items-center gap-3">
            <img src="/factory-mark.svg" alt="" className="size-7" aria-hidden />
            <span className="font-[family-name:var(--font-head)] text-sm font-extrabold tracking-tight text-brand">
              Roxabi Factory
            </span>
            <span className="mono text-[10px] uppercase text-muted-foreground">dashboard</span>
          </div>
          <ThemeToggle />
        </div>
      </header>
      <main className="relative z-10">{children}</main>
    </div>
  );
}
