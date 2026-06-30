import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as adminApi from "@/lib/admin-api";
import { AdminPage } from "@/pages/AdminPage";

function renderAdmin() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: AdminPage });
  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: ["/"] }),
    context: { queryClient: undefined as unknown as QueryClient },
  });
  void router.load();
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("AdminPage", () => {
  beforeEach(() => {
    vi.spyOn(adminApi, "fetchAdminAccess").mockResolvedValue({
      users: [
        {
          user_id: "rx:user:abc123",
          display_name: "Mickael",
          email: "mickael@roxabi.dev",
          telegram: {
            platform: "telegram",
            platform_uid: "7377831990",
            platform_key: "tg:user:7377831990",
          },
          discord: null,
          agents: ["lyra"],
        },
      ],
    });
  });

  it("renders user access rows", async () => {
    renderAdmin();
    await waitFor(() => {
      expect(screen.getByText("Mickael")).toBeTruthy();
    });
    expect(screen.getByText("mickael@roxabi.dev")).toBeTruthy();
    expect(screen.getByText("Oui")).toBeTruthy();
    expect(screen.getByText("Non")).toBeTruthy();
    expect(screen.getByText("lyra")).toBeTruthy();
  });

  it("shows create and edit actions", async () => {
    renderAdmin();
    await waitFor(() => expect(screen.getByText("Mickael")).toBeTruthy());
    expect(screen.getByRole("button", { name: /nouvel utilisateur/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /éditer/i })).toBeTruthy();
  });
});
