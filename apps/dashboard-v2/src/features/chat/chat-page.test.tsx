import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as chatApi from "@/features/chat/api";
import { ChatPage } from "@/features/chat/chat-page";
import { renderWithRouter } from "@/test-utils/router";

describe("ChatPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(chatApi, "fetchAgentStatus").mockResolvedValue([]);
    vi.spyOn(chatApi, "fetchSessions").mockResolvedValue([]);
  });

  it("shows error when agents fetch fails", async () => {
    vi.spyOn(chatApi, "fetchAgents").mockRejectedValue(new Error("agents down"));
    const { ui } = renderWithRouter(<ChatPage />);
    render(ui);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(
      "Failed to load agents. Chat is unavailable until the roster can be fetched.",
    );
  });
});
