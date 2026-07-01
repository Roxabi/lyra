import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UserFormDialog } from "@/components/admin/UserFormDialog";
import * as adminApi from "@/lib/admin-api";
import * as agentsApi from "@/lib/agents-api";
import { BffApiError } from "@/lib/bff-api";

const toastSuccess = vi.fn();
const toastError = vi.fn();

vi.mock("@/components/ui/sonner", () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
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
    toastSuccess.mockReset();
    toastError.mockReset();
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
    expect(toastSuccess).toHaveBeenCalledWith("Utilisateur créé.");
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
      expect(toastError).toHaveBeenCalledWith("Cet email est déjà enregistré.");
    });
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