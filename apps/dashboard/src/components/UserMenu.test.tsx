import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { UserMenu } from "@/components/UserMenu";
import "@/i18n";

function TestPage() {
  return <UserMenu variant="compact" />;
}

function renderUserMenu() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: TestPage });
  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: ["/"] }),
    context: { queryClient: undefined as unknown as QueryClient },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  void router.load();
  return view;
}

describe("UserMenu", () => {
  it("opens the menu with profile and navigation links", async () => {
    const user = userEvent.setup();
    renderUserMenu();

    const trigger = await waitFor(() => screen.getByRole("button", { name: /opérateur factory/i }));
    await user.click(trigger);

    // Astryx renders the Popover navigation entries as Items (button semantics),
    // not Radix-generated `role="menuitem"` nodes — assert by stable data-testid
    // instead of the framework-coupled role that the old dropdown emitted.
    expect(screen.getByText("operator@roxabi.dev")).toBeTruthy();
    expect(screen.getByTestId("user-menu-users").textContent).toMatch(/utilisateurs/i);
    expect(screen.getByTestId("user-menu-design-system").textContent).toMatch(/design system/i);
  });
});
