import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { AdminPage } from "@/pages/AdminPage";
import { AgentDetailPage, AgentsListPage } from "@/pages/AgentsPage";
import { ChatPage } from "@/pages/ChatPage";
import { DashboardHome } from "@/pages/DashboardHome";
import { DesignSystemPage } from "@/pages/DesignSystemPage";
import { FleetPage } from "@/pages/FleetPage";
import { IntegrationsPage } from "@/pages/IntegrationsPage";
import { JobsPage } from "@/pages/JobsPage";
import { OpsPage } from "@/pages/OpsPage";

interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: AppShell,
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

const integrationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/integrations",
  component: IntegrationsPage,
});

const obsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ops",
  validateSearch: (search: Record<string, unknown>) => ({
    container:
      typeof search.container === "string" && search.container.trim()
        ? search.container.trim()
        : undefined,
  }),
  component: OpsPage,
});

const agentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agents",
  component: AgentsListPage,
});

const agentDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agents/$name",
  component: AgentDetailPage,
});

const fleetRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/fleet",
  component: FleetPage,
});

const designSystemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/design-system",
  component: DesignSystemPage,
});

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/users",
  component: AdminPage,
});

const adminRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/admin",
  beforeLoad: () => {
    throw redirect({ to: "/users" });
  },
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  chatRoute,
  jobsRoute,
  integrationsRoute,
  agentsRoute,
  agentDetailRoute,
  fleetRoute,
  obsRoute,
  usersRoute,
  adminRedirectRoute,
  designSystemRoute,
]);

export const router = createRouter({
  routeTree,
  context: { queryClient: undefined as unknown as QueryClient },
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
