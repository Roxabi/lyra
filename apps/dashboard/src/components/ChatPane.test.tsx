import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatPane } from "@/components/ChatPane";
import type { ChatTab } from "@/lib/chats-storage";

const tab: ChatTab = {
  id: "1",
  agent: "lyra",
  harness: "claude-cli",
  model: "sonnet",
  sessionId: null,
  streamToken: null,
  lastActive: 0,
};

describe("ChatPane", () => {
  it("resets model to harness default when switching to omp-rpc", async () => {
    const user = userEvent.setup();
    const onUpdate = vi.fn();
    render(<ChatPane tab={tab} health={undefined} onUpdate={onUpdate} />);

    await user.selectOptions(screen.getByLabelText("Harness"), "omp-rpc");

    expect(onUpdate).toHaveBeenCalledWith({
      harness: "omp-rpc",
      model: "omp-default",
    });
  });
});
