import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UserFormDialog } from "@/components/admin/UserFormDialog";
import * as adminApi from "@/lib/admin-api";
import * as agentsApi from "@/lib/agents-api";
import { BffApiError } from "@/lib/bff-api";

const showToast = vi.fn();

vi.mock("@astryxdesign/core/Toast", () => ({
  useToast: () => showToast,
}));

function renderDialog(props?: Partial<Parameters<typeof UserFormDialog>[0]>) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <UserFormDialog open onOpenChange={onOpenChange} {...props} />
    </QueryClientProvider>,
  );
  return { onOpenChange };
}

describe("UserFormDialog", () => {
  beforeEach(() => {
    showToast.mockReset();
    vi.spyOn(agentsApi, "fetchAgentsConfigList").mockResolvedValue({
      agents: [
        {
          name: "lyra",
          backend: "claude-cli",
          model: "sonnet",
          updated_at: "2026-01-01T00:00:00Z",
          soul_document_bytes: null,
          has_soul: false,
          has_telegram: false,
          has_discord: false,
          has_email: false,
        },
      ],
    });
  });

  it("creates a user and shows success toast", async () => {
    const user = userEvent.setup();
    const createAdminUser = vi.spyOn(adminApi, "createAdminUser").mockResolvedValue({
      user_id: "rx:user:new",
      display_name: "Ops",
      email: "ops@example.com",
      telegram: null,
      discord: null,
      agents: ["lyra"],
    });
    const { onOpenChange } = renderDialog();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /lyra/i })).toBeTruthy();
    });

    await user.type(screen.getByLabelText(/nom/i), "Ops");
    await user.type(screen.getByLabelText(/email/i), "ops@example.com");
    await user.click(screen.getByRole("button", { name: /lyra/i }));
    await user.click(screen.getByRole("button", { name: /créer/i }));

    await waitFor(() => {
      expect(createAdminUser).toHaveBeenCalledWith({
        display_name: "Ops",
        email: "ops@example.com",
        telegram_uid: null,
        discord_uid: null,
        agents: ["lyra"],
      });
    });
    expect(showToast).toHaveBeenCalledWith({ body: "Utilisateur créé.", type: "info" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("shows a specific toast on email conflict", async () => {
    const user = userEvent.setup();
    vi.spyOn(adminApi, "createAdminUser").mockRejectedValue(
      new BffApiError(409, "email already registered: ops@example.com"),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/nom/i), "Ops");
    await user.type(screen.getByLabelText(/email/i), "ops@example.com");
    await user.click(screen.getByRole("button", { name: /créer/i }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith({
        body: "Cet email est déjà enregistré.",
        type: "error",
      });
    });
  });

  it("shows platform conflict toast on create", async () => {
    const user = userEvent.setup();
    vi.spyOn(adminApi, "createAdminUser").mockRejectedValue(
      new BffApiError(409, "platform identity already linked: tg:user:99999"),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/nom/i), "Ops");
    await user.type(screen.getByLabelText(/email/i), "ops@example.com");
    await user.type(screen.getByLabelText(/telegram/i), "99999");
    await user.click(screen.getByRole("button", { name: /créer/i }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith({
        body: "Cette identité plateforme est déjà liée à un autre utilisateur.",
        type: "error",
      });
    });
  });

  it("shows edit-specific toast on patch conflict", async () => {
    const user = userEvent.setup();
    vi.spyOn(adminApi, "patchAdminUser").mockRejectedValue(
      new BffApiError(409, "email already registered"),
    );
    renderDialog({
      user: {
        user_id: "rx:user:abc",
        display_name: "Jane",
        email: "jane@example.com",
        telegram: null,
        discord: null,
        agents: [],
      },
    });

    await user.click(screen.getByRole("button", { name: /enregistrer/i }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith({
        body: "Cet email est déjà enregistré.",
        type: "error",
      });
    });
  });

  it("closes via the Astryx DialogHeader close button", async () => {
    const user = userEvent.setup();
    const { onOpenChange } = renderDialog();
    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("prefills fields when editing an existing user", async () => {
    renderDialog({
      user: {
        user_id: "rx:user:abc",
        display_name: "Jane",
        email: "jane@example.com",
        telegram: {
          platform: "telegram",
          platform_uid: "12345",
          platform_key: "tg:user:12345",
        },
        discord: null,
        agents: ["lyra"],
      },
    });

    await waitFor(() => {
      expect(screen.getByDisplayValue("Jane")).toBeTruthy();
    });
    expect(screen.getByDisplayValue("jane@example.com")).toBeTruthy();
    expect(screen.getByDisplayValue("12345")).toBeTruthy();
    expect(screen.getByRole("button", { name: /enregistrer/i })).toBeTruthy();
  });
});
