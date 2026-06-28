import { Outlet, useRouterState } from "@tanstack/react-router";
import { AppHeader } from "@/components/layout/AppHeader";
import { AppSidebar } from "@/components/layout/AppSidebar";
import { appNavItems, resolveNavFlags } from "@/lib/nav";
import { ShellTitleProvider } from "@/lib/shell-title";
import { cn } from "@/lib/utils";

export function AppShell() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { fullBleed, wideLayout } = resolveNavFlags(pathname, appNavItems);

  return (
    <ShellTitleProvider key={pathname}>
      <div className="ember-canvas flex h-dvh overflow-hidden bg-background text-foreground">
        <AppSidebar />

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <AppHeader />

          <main
            className={cn(
              "min-h-0 flex-1",
              fullBleed
                ? "overflow-hidden p-0"
                : "fd-scroll overflow-y-auto px-4 py-4 md:px-6 md:py-6",
            )}
          >
            <div
              className={cn(
                "mx-auto w-full",
                fullBleed
                  ? "h-full min-h-0 max-w-none"
                  : wideLayout
                    ? "max-w-6xl xl:max-w-7xl"
                    : "max-w-5xl",
              )}
            >
              <Outlet />
            </div>
          </main>
        </div>
      </div>
    </ShellTitleProvider>
  );
}
