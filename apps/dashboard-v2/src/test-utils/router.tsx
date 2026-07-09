import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import type { ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";

export function renderWithRouter(ui: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: () => ui });
  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: ["/"] }),
    context: { queryClient: undefined as unknown as QueryClient },
  });
  void router.load();
  return {
    queryClient,
    router,
    ui: (
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </I18nextProvider>
    ),
  };
}
