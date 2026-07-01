import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatPane } from "@/components/ChatPane";
import type { ChatTab } from "@/lib/chats-storage";

const tab: ChatTab = {
  id: "1",
  agent: "lyra_default",
  harness: "claude-cli",
  model: "sonnet",
  sessionId: null,
  streamToken: null,
  lastActive: 0,
};

describe("ChatPane", () => {
  it("displays friendly agent name", () => {
    render(<ChatPane tab={tab} health={undefined} onUpdate={vi.fn()} />);
    expect(screen.getByText("Lyra")).toBeTruthy();
  });

  it("resets model to harness default when switching to omp-rpc", async () => {
    const user = userEvent.setup();
    const onUpdate = vi.fn();
    render(<ChatPane tab={tab} health={undefined} onUpdate={onUpdate} />);
    // ChatPane renders two Selector comboboxes (harness + model); the harness
    // one carries the "Harness" accessible name (label is identical in en/fr).
    await user.click(screen.getByRole("combobox", { name: "Harness" }));
    await user.click(screen.getByRole("option", { name: /OMP/i }));
    expect(onUpdate).toHaveBeenCalledWith({
      harness: "omp-rpc",
      model: "omp-default",
    });
  });
});
