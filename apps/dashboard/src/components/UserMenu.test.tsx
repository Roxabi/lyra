import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { UserMenu } from "@/components/UserMenu";
import "@/i18n";

function Root() {
  return (
    <>
      <UserMenu variant="compact" />
      <Outlet />
    </>
  );
}

function renderUserMenu() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: Root });
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => null,
  });
  const usersRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/users",
    component: () => <div>users-page</div>,
  });
  const designRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/design-system",
    component: () => <div>design-page</div>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, usersRoute, designRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
    context: { queryClient: undefined as unknown as QueryClient },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  void router.load();
  return { ...view, router };
}

describe("UserMenu", () => {
  it("opens the menu on trigger click, exposing profile and nav links", async () => {
    const user = userEvent.setup();
    renderUserMenu();

    const trigger = await waitFor(() => screen.getByRole("button", { name: /opérateur factory/i }));
    // `aria-expanded` is the click-dependent signal — Astryx's Popover mounts its
    // content unconditionally (and jsdom applies no `[popover]{display:none}`), so
    // asserting DOM presence alone would pass even if the trigger were dead. Astryx
    // sets aria-expanded imperatively, so this flips only when the click fires.
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    expect(screen.getByText("operator@roxabi.dev")).toBeTruthy();
    expect(screen.getByTestId("user-menu-users").textContent).toMatch(/utilisateurs/i);
    expect(screen.getByTestId("user-menu-design-system").textContent).toMatch(/design system/i);
  });

  it("navigates and closes the menu when a nav link is clicked", async () => {
    const user = userEvent.setup();
    const { router } = renderUserMenu();

    const trigger = await waitFor(() => screen.getByRole("button", { name: /opérateur factory/i }));
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    await user.click(screen.getByTestId("user-menu-users"));

    // handleNavigate must both route and close the popover (Radix closed on
    // select for free; the Popover port has to do it explicitly).
    await waitFor(() => expect(router.state.location.pathname).toBe("/users"));
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });
});
