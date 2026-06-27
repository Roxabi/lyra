import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MultiChatTabs } from "@/components/MultiChatTabs";

describe("MultiChatTabs", () => {
  it("renders tabs and new button", () => {
    render(
      <MultiChatTabs
        tabs={[
          {
            id: "1",
            agent: "lyra",
            harness: "claude-cli",
            model: "sonnet",
            sessionId: null,
            streamToken: null,
            lastActive: 0,
          },
        ]}
        activeId="1"
        onSelect={vi.fn()}
        onClose={vi.fn()}
        onNew={vi.fn()}
      />,
    );
    expect(screen.getByText("+ New chat")).toBeTruthy();
    expect(screen.getByText("lyra")).toBeTruthy();
  });
});
