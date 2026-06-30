import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { IntegrationsPage } from "@/pages/IntegrationsPage";

function renderIntegrations() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <IntegrationsPage />
    </QueryClientProvider>,
  );
}

describe("IntegrationsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    vi.spyOn(api, "fetchConnectors").mockResolvedValue({
      connectors: [
        { name: "github", family: "centralized_app", label: "GitHub App" },
        { name: "cloudflare", family: "per_account", label: "Cloudflare Notifications" },
      ],
      factory_tenant: "default",
    });
    vi.spyOn(api, "fetchGithubInstallUrl").mockResolvedValue({
      url: "https://github.com/apps/factory-e2e/installations/new",
      app_slug: "factory-e2e",
    });
    vi.spyOn(api, "fetchConnectorInstallations").mockImplementation(async (connector) => ({
      installations: [
        {
          connector,
          external_id: "4242",
          factory_tenant: "default",
          enabled: true,
        },
      ],
    }));
  });

  it("renders connector sections and installation rows", async () => {
    renderIntegrations();
    await waitFor(() => {
      expect(screen.getByText("GitHub App")).toBeTruthy();
      expect(screen.getByText("Cloudflare")).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getAllByText("4242").length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByRole("link", { name: "Installer sur GitHub" })).toBeTruthy();
  });

  it("shows auth error when catalog fetch returns 401", async () => {
    vi.mocked(api.fetchConnectors).mockRejectedValue(new Error("auth"));
    renderIntegrations();
    await waitFor(() => {
      expect(screen.getByText("Jeton opérateur invalide ou manquant.")).toBeTruthy();
    });
  });
});
