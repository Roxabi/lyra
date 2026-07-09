import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as chatApi from "@/features/chat/api";
import { ChatSidebar } from "@/features/chat/components/chat-sidebar";
import i18n from "@/i18n";

function renderSidebar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <I18nextProvider i18n={i18n}>
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
      </QueryClientProvider>
    </I18nextProvider>,
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
    expect(alert.textContent).toContain(i18n.t("chat:sessionsLoadError"));
  });
});
