import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { AppShell } from "@/components/app-shell/app-shell";
import { AgentDetailPage } from "@/features/agents/agent-detail-page";
import { AgentsListPage } from "@/features/agents/agents-list-page";
import { ChatPage } from "@/features/chat/chat-page";
import { FleetPage } from "@/features/fleet/fleet-page";
import { IntegrationsPage } from "@/features/integrations/integrations-page";
import { JobsPage } from "@/features/jobs/jobs-page";
import { OpsPage } from "@/features/ops/ops-page";
import { OverviewPage } from "@/features/overview/overview-page";
import { PipelinePage } from "@/features/pipeline/pipeline-page";
import { SpansPage } from "@/features/spans/spans-page";
import { UsersPage } from "@/features/users/users-page";
import { DesignSystemPage } from "@/pages/design-system-page";

interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: AppShell,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: OverviewPage,
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

const opsRoute = createRoute({
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

const spansRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/spans",
  component: SpansPage,
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

const pipelineRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/pipeline",
  component: PipelinePage,
});

const designSystemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/design-system",
  component: DesignSystemPage,
});

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/users",
  component: UsersPage,
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
  pipelineRoute,
  fleetRoute,
  opsRoute,
  spansRoute,
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
