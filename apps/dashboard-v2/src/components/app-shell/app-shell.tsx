import { Outlet, useRouterState } from "@tanstack/react-router";
import { useEffect } from "react";
import { resolveLayoutFlags } from "@/app/nav";
import { ShellTitleProvider } from "@/app/shell-title";
import { AppHeader } from "@/components/app-shell/app-header";
import { AppSidebar } from "@/components/app-shell/app-sidebar";
import { SidebarInset, useSidebar } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { cn } from "@/lib/utils";

function ShellLayout() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { fullBleed, wide } = resolveLayoutFlags(pathname);
  const { setOpenMobile, isMobile } = useSidebar();

  useEffect(() => {
    if (isMobile) setOpenMobile(false);
  }, [isMobile, setOpenMobile]);

  return (
    <ShellTitleProvider key={pathname}>
      <AppHeader />
      <main className="flex min-h-0 flex-1 flex-col">
        <div
          className={cn(
            "mx-auto w-full flex-1",
            fullBleed
              ? "flex h-full min-h-0 max-w-none flex-col overflow-hidden p-0"
              : cn("px-4 py-4 md:px-6 md:py-6", wide ? "max-w-6xl xl:max-w-7xl" : "max-w-5xl"),
          )}
        >
          <Outlet />
        </div>
      </main>
    </ShellTitleProvider>
  );
}

export function AppShell() {
  return (
    <>
      <AppSidebar />
      <SidebarInset className="min-h-dvh">
        <ShellLayout />
      </SidebarInset>
      <Toaster />
    </>
  );
}
