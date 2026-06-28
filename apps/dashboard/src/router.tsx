import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { ChatPage } from "@/pages/ChatPage";
import { DashboardHome } from "@/pages/DashboardHome";
import { JobsPage } from "@/pages/JobsPage";
import { OpsPage } from "@/pages/OpsPage";

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

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: RootLayout,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: DashboardHome,
});

const chatRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/chat",
  component: ChatPage,
});

const jobsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs",
  component: JobsPage,
});

const obsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ops",
  component: OpsPage,
});

const routeTree = rootRoute.addChildren([indexRoute, chatRoute, jobsRoute, obsRoute]);

export const router = createRouter({
  routeTree,
  context: { queryClient: undefined as unknown as QueryClient },
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
