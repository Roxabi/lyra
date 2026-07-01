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
    // ChatPane renders two Selector comboboxes (harness + model); pick the
    // harness one by its displayed value.
    const harnessCombobox = screen
      .getAllByRole("combobox")
      .find((cb) => cb.textContent?.includes("Clipool"));
    if (!harnessCombobox) throw new Error("harness combobox not found");
    await user.click(harnessCombobox);
    await user.click(screen.getByRole("option", { name: /OMP/i }));
    expect(onUpdate).toHaveBeenCalledWith({
      harness: "omp-rpc",
      model: "omp-default",
    });
  });
});
