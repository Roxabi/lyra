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
  it("opens dropdown with profile and admin links", async () => {
    const user = userEvent.setup();
    renderUserMenu();

    const trigger = await waitFor(() =>
      screen.getByRole("button", { name: /opérateur factory/i }),
    );
    await user.click(trigger);
    expect(screen.getByText("operator@roxabi.dev")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /utilisateurs/i })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /design system/i })).toBeTruthy();
  });
});