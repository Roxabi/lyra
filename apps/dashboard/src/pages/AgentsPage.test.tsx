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
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as agentsApi from "@/lib/agents-api";
import { ShellTitleProvider } from "@/lib/shell-title";
import { AgentDetailPage, AgentsListPage } from "@/pages/AgentsPage";

function renderAgentsList() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: AgentsListPage });
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

function renderAgentDetail() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute();
  const detailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agents/$name",
    component: AgentDetailPage,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([detailRoute]),
    history: createMemoryHistory({ initialEntries: ["/agents/lyra"] }),
    context: { queryClient: undefined as unknown as QueryClient },
  });
  void router.load();
  return render(
    <QueryClientProvider client={queryClient}>
      <ShellTitleProvider>
        <RouterProvider router={router} />
      </ShellTitleProvider>
    </QueryClientProvider>,
  );
}

const CONFIG = {
  name: "lyra",
  backend: "claude-cli" as const,
  model: "sonnet",
  voice_json: null,
  soul_meta_json: { header: { display_name: "Lyra", tagline: "ops" } },
  soul_document_blob_ref: "sha256:abc",
  soul_document_bytes: 100,
  updated_at: "2026-06-29T12:00:00Z",
};

describe("AgentsListPage", () => {
  it("shows create agent button", async () => {
    vi.spyOn(agentsApi, "fetchAgentsConfigList").mockResolvedValue({ agents: [] });
    renderAgentsList();
    expect(await screen.findByRole("button", { name: /nouvel agent/i })).toBeTruthy();
  });

  beforeEach(() => {
    vi.spyOn(agentsApi, "fetchAgentsConfigList").mockResolvedValue({
      agents: [
        {
          name: "lyra",
          backend: "claude-cli",
          model: "sonnet",
          updated_at: "2026-06-29T12:00:00Z",
          soul_document_bytes: 1200,
          has_soul: true,
          has_telegram: true,
          has_discord: false,
          has_email: false,
        },
      ],
    });
  });

  it("renders agent list with harness and model", async () => {
    renderAgentsList();
    await waitFor(() => {
      expect(screen.getByText("Lyra")).toBeTruthy();
    });
    expect(screen.getByText(/Clipool/)).toBeTruthy();
    expect(screen.getByText(/sonnet/)).toBeTruthy();
  });

  it("switches between card and table views with toolbar search", async () => {
    const user = userEvent.setup();
    renderAgentsList();
    await waitFor(() => expect(screen.getByText("Lyra")).toBeTruthy());
    expect(screen.getByPlaceholderText("Rechercher un agent…")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /Vue tableau/i }));
    expect(screen.getByRole("columnheader", { name: "Tagline" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Harness" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Telegram" })).toBeTruthy();
    expect(screen.getByText("Oui")).toBeTruthy();
    expect(screen.getAllByText("Éditer").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /Vue cartes/i }));
    expect(screen.getByText("Clipool")).toBeTruthy();
  });
});

describe("AgentDetailPage", () => {
  beforeEach(() => {
    vi.spyOn(agentsApi, "fetchAgentConfig").mockResolvedValue(CONFIG);
    vi.spyOn(agentsApi, "fetchAgentSoul").mockResolvedValue({
      sections: { Identity: "I am Lyra", Personality: "Warm" },
      updated_at: "2026-06-29T12:00:00Z",
    });
    vi.spyOn(agentsApi, "previewAgentSoul").mockResolvedValue({
      composed: "## Identity\nI am Lyra\n\n## Voice messages\nWhen",
      truncated: false,
    });
    vi.spyOn(agentsApi, "patchAgentConfig").mockResolvedValue(CONFIG);
    vi.spyOn(agentsApi, "putAgentSoul").mockResolvedValue({});
  });

  it("renders five soul section tabs and dirty banner on edit", async () => {
    const user = userEvent.setup();
    renderAgentDetail();
    await waitFor(() => {
      expect(screen.getByText(/lyra/)).toBeTruthy();
    });
    expect(screen.getByText("Identity")).toBeTruthy();
    expect(screen.getByText("Guidelines")).toBeTruthy();
    expect(screen.getByText(/Délai session/)).toBeTruthy();

    const soulTextarea = screen.getAllByRole("textbox")[2];
    await user.clear(soulTextarea);
    await user.type(soulTextarea, "Updated identity");
    expect(screen.getByText(/Modifications non enregistrées/)).toBeTruthy();
  });

  it("preview compose calls hub RPC", async () => {
    const user = userEvent.setup();
    renderAgentDetail();
    await waitFor(() => expect(screen.getByText("Aperçu composition")).toBeTruthy());
    await user.click(screen.getByText("Aperçu composition"));
    await waitFor(() => {
      expect(agentsApi.previewAgentSoul).toHaveBeenCalledWith("lyra", {
        sections: expect.objectContaining({ Identity: "I am Lyra" }),
      });
    });
  });

  it("save persists config and soul", async () => {
    const user = userEvent.setup();
    renderAgentDetail();
    await waitFor(() => expect(screen.getByText("Enregistrer")).toBeTruthy());
    const soulTextarea = screen.getAllByRole("textbox")[2];
    await user.type(soulTextarea, " edit");
    await user.click(screen.getByText("Enregistrer"));
    await waitFor(() => {
      expect(agentsApi.patchAgentConfig).toHaveBeenCalled();
      expect(agentsApi.putAgentSoul).toHaveBeenCalled();
    });
  });

  it("shows secret lint warning when soul text matches token patterns", async () => {
    const user = userEvent.setup();
    renderAgentDetail();
    await waitFor(() => expect(screen.getByText("Identity")).toBeTruthy());
    const soulTextarea = screen.getAllByRole("textbox")[2];
    await user.clear(soulTextarea);
    await user.type(soulTextarea, "key sk-abcdefghijklmnopqrstuvwx");
    expect(screen.getByText(/Possible secret detected/)).toBeTruthy();
  });
});