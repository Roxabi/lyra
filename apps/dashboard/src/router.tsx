import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { CockpitLayout } from "@/components/CockpitLayout";
import { PanelMount } from "@/components/PanelMount";

interface RouterContext {
  queryClient: QueryClient;
}

function RootLayout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}

function HomePage() {
  return <CockpitLayout />;
}

function JobsStub() {
  return (
    <div className="p-6">
      <PanelMount id="jobs" title="Jobs panel" />
    </div>
  );
}

function ObsStub() {
  return (
    <div className="p-6">
      <PanelMount id="obs" title="Obs panel" />
    </div>
  );
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: RootLayout,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: HomePage,
});

const jobsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/panels/jobs",
  component: JobsStub,
});

const obsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/panels/obs",
  component: ObsStub,
});

const routeTree = rootRoute.addChildren([indexRoute, jobsRoute, obsRoute]);

export const router = createRouter({
  routeTree,
  context: { queryClient: undefined as unknown as QueryClient },
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
