import { AppShell as AstryxAppShell } from "@astryxdesign/core/AppShell";
import { LinkProvider } from "@astryxdesign/core/Link";
import { Outlet, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppSideNav } from "@/components/layout/AppSideNav";
import { AppTopNav } from "@/components/layout/AppTopNav";
import { RouterLink } from "@/components/layout/RouterLink";
import { Toaster } from "@/components/ui/sonner";
import { resolveNavFlags } from "@/lib/nav";
import { ShellTitleProvider } from "@/lib/shell-title";
import { cn } from "@/lib/utils";

export function AppShell() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { fullBleed, wideLayout } = resolveNavFlags(pathname);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Astryx's AppShell auto-generates the mobile drawer from `sideNav` (plus
  // `topNav`'s own content) below the `md` breakpoint. Controlling `isOpen`
  // ourselves — instead of leaving it uncontrolled — lets us close the
  // drawer on every route change (acceptance criterion for #2090).
  // biome-ignore lint/correctness/useExhaustiveDependencies: pathname is the trigger — the effect must re-run on every navigation to close the drawer; removing it would fire only once on mount.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  return (
    <ShellTitleProvider key={pathname}>
      <LinkProvider component={RouterLink}>
        <AstryxAppShell
          className="ember-canvas bg-background text-foreground"
          topNav={<AppTopNav />}
          sideNav={<AppSideNav />}
          mobileNav={{ isOpen: mobileNavOpen, onOpenChange: setMobileNavOpen }}
        >
          <div
            className={cn(
              "mx-auto w-full",
              fullBleed
                ? "h-full min-h-0 max-w-none p-0"
                : cn(
                    "px-4 py-4 md:px-6 md:py-6",
                    wideLayout ? "max-w-6xl xl:max-w-7xl" : "max-w-5xl",
                  ),
            )}
          >
            <Outlet />
          </div>
        </AstryxAppShell>
      </LinkProvider>
      <Toaster />
    </ShellTitleProvider>
  );
}
