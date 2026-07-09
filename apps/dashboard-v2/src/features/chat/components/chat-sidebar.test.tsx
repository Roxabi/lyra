import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as chatApi from "@/features/chat/api";
import { ChatSidebar } from "@/features/chat/components/chat-sidebar";

function renderSidebar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatSidebar
        tabs={[
          {
            id: "tab-1",
            agent: "lyra",
            harness: "claude-cli",
            model: "sonnet",
            sessionId: null,
            streamToken: null,
            lastActive: Date.now(),
          },
        ]}
        activeId="tab-1"
        agents={["lyra"]}
        healthByAgent={new Map()}
        onSelect={() => {}}
        onClose={() => {}}
        onNew={() => {}}
        onResumed={() => {}}
      />
    </QueryClientProvider>,
  );
}

describe("ChatSidebar", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows error when sessions fetch fails", async () => {
    vi.spyOn(chatApi, "fetchSessions").mockRejectedValue(new Error("sessions down"));
    renderSidebar();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Failed to load sessions.");
  });
});
