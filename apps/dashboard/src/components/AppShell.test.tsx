import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AppShell } from "@/components/AppShell";
import { appNavItems } from "@/lib/nav";

// Note: the mobile hamburger drawer's open/close-on-route-change (acceptance
// criterion for #2090) is NOT unit-tested here — jsdom implements neither
// `HTMLDialogElement.showModal()` (Astryx MobileNav) nor real media queries, so
// driving the drawer would be unreliable. It is covered by the controlled
// `mobileNav.isOpen` + route-change effect in AppShell.tsx and the e2e/visual
// pass. These tests cover the desktop shell: nav rendering, active state, and
// the RouterLink navigation seam.

function renderShell(initialPath = "/") {
  const rootRoute = createRootRoute({ component: AppShell });
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <div data-testid="page">home</div>,
  });
  const otherRoutes = appNavItems
    .filter((item) => item.to !== "/")
    .map((item) =>
      createRoute({
        getParentRoute: () => rootRoute,
        path: item.to,
        component: () => <div data-testid="page">{item.to}</div>,
      }),
    );
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, ...otherRoutes]),
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      {/* biome-ignore lint/suspicious/noExplicitAny: test stub router tree isn't the app's typed registry */}
      <RouterProvider router={router as any} />
    </QueryClientProvider>,
  );
  return { ...utils, router };
}

function navHrefs(): string[] {
  return Array.from(document.querySelectorAll("a[href]")).map((a) => a.getAttribute("href") ?? "");
}

describe("AppShell (Astryx shell)", () => {
  it("renders every nav item as a link", async () => {
    renderShell("/");
    await screen.findByTestId("page");
    const hrefs = navHrefs();
    for (const item of appNavItems) {
      expect(hrefs).toContain(item.to);
    }
  });

  it("marks the active route's nav item with aria-current=page", async () => {
    renderShell("/agents");
    await screen.findByText("/agents");
    const active = document.querySelector('a[href="/agents"]');
    expect(active?.getAttribute("aria-current")).toBe("page");
    const inactive = document.querySelector('a[href="/jobs"]');
    expect(inactive?.getAttribute("aria-current")).toBeNull();
  });

  it("navigates through the RouterLink adapter when a nav item is clicked", async () => {
    const user = userEvent.setup();
    const { router } = renderShell("/");
    await screen.findByText("home");
    const link = document.querySelector('a[href="/agents"]') as HTMLElement;
    await user.click(link);
    await waitFor(() => expect(router.state.location.pathname).toBe("/agents"));
  });
});
