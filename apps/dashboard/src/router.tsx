import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, createRoute, createRouter } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { AgentDetailPage, AgentsListPage } from "@/pages/AgentsPage";
import { ChatPage } from "@/pages/ChatPage";
import { DashboardHome } from "@/pages/DashboardHome";
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

const routeTree = rootRoute.addChildren([
  indexRoute,
  chatRoute,
  jobsRoute,
  integrationsRoute,
  agentsRoute,
  agentDetailRoute,
  fleetRoute,
  obsRoute,
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
