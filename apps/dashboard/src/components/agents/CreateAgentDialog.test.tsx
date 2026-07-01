import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CreateAgentDialog } from "@/components/agents/CreateAgentDialog";
import * as agentsApi from "@/lib/agents-api";
import { BffApiError } from "@/lib/bff-api";

const showToast = vi.fn();
const navigate = vi.fn();

vi.mock("@astryxdesign/core/Toast", () => ({
  useToast: () => showToast,
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

function renderDialog() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <CreateAgentDialog open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  );
  return { onOpenChange };
}

describe("CreateAgentDialog", () => {
  beforeEach(() => {
    showToast.mockReset();
    navigate.mockReset();
  });

  it("creates an agent and navigates to its detail page", async () => {
    const user = userEvent.setup();
    const createAgentConfig = vi.spyOn(agentsApi, "createAgentConfig").mockResolvedValue({
      name: "scout",
      backend: "claude-cli",
      model: "sonnet",
      voice_json: null,
      soul_meta_json: null,
      soul_document_blob_ref: null,
      soul_document_bytes: null,
      updated_at: "2026-01-01T00:00:00Z",
    });
    const { onOpenChange } = renderDialog();

    await user.type(screen.getByLabelText(/identifiant/i), "scout");
    await user.click(screen.getByRole("button", { name: /^créer$/i }));

    await waitFor(() => {
      expect(createAgentConfig).toHaveBeenCalledWith({
        name: "scout",
        backend: "claude-cli",
        model: "sonnet",
        display_name: "scout",
        tagline: "",
      });
    });
    expect(showToast).toHaveBeenCalledWith({ body: "Agent scout créé.", type: "info" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(navigate).toHaveBeenCalledWith({
      to: "/agents/$name",
      params: { name: "scout" },
    });
  });

  it("shows a specific toast when the agent identifier already exists", async () => {
    const user = userEvent.setup();
    vi.spyOn(agentsApi, "createAgentConfig").mockRejectedValue(
      new BffApiError(409, "agent 'scout' already exists"),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/identifiant/i), "scout");
    await user.click(screen.getByRole("button", { name: /^créer$/i }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith({
        body: "Un agent avec cet identifiant existe déjà.",
        type: "error",
      });
    });
  });

  it("blocks submit for invalid slugs", async () => {
    const user = userEvent.setup();
    const createAgentConfig = vi.spyOn(agentsApi, "createAgentConfig");
    renderDialog();

    await user.type(screen.getByLabelText(/identifiant/i), "Bad Agent");
    const submit = screen.getByRole("button", { name: /^créer$/i });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    expect(createAgentConfig).not.toHaveBeenCalled();
  });

  it("closes via the Astryx DialogHeader close button", async () => {
    const user = userEvent.setup();
    const { onOpenChange } = renderDialog();
    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
