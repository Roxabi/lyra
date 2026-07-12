import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  Outlet,
  redirect,
  useRouterState,
} from "@tanstack/react-router";
import { AppShell } from "@/components/app-shell/app-shell";
import { AgentDetailPage } from "@/features/agents/agent-detail-page";
import { AgentsListPage } from "@/features/agents/agents-list-page";
import { AcceptInvitePage } from "@/features/auth/accept-invite-page";
import { AccountLinksPage } from "@/features/auth/account-links-page";
import { fetchMe } from "@/features/auth/api";
import { LoginPage } from "@/features/auth/login-page";
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

const PUBLIC_PATHS = new Set(["/login", "/accept-invite"]);

function RootLayout() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  if (PUBLIC_PATHS.has(pathname)) {
    return <Outlet />;
  }
  return <AppShell />;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: RootLayout,
  beforeLoad: async ({ location }) => {
    if (PUBLIC_PATHS.has(location.pathname)) return;
    const safeRedirect =
      location.pathname.startsWith("/") && !location.pathname.startsWith("//")
        ? `${location.pathname}${location.searchStr ?? ""}`
        : "/";
    try {
      const me = await fetchMe();
      if (me === null) {
        throw redirect({
          to: "/login",
          search: { redirect: safeRedirect },
        });
      }
    } catch (e) {
      if (e && typeof e === "object" && "to" in e) throw e;
      // Fail closed on network/5xx — do not mount protected shell.
      throw redirect({
        to: "/login",
        search: { redirect: safeRedirect },
      });
    }
  },
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: OverviewPage,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
  validateSearch: (search: Record<string, unknown>) => ({
    redirect: typeof search.redirect === "string" ? search.redirect : undefined,
  }),
});

const acceptInviteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/accept-invite",
  component: AcceptInvitePage,
  validateSearch: (search: Record<string, unknown>) => ({
    token: typeof search.token === "string" ? search.token : undefined,
  }),
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

const accountLinksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/account/links",
  component: AccountLinksPage,
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
  loginRoute,
  acceptInviteRoute,
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
  accountLinksRoute,
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
