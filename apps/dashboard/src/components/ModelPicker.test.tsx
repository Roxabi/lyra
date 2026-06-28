import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ModelPicker } from "@/components/ModelPicker";

describe("ModelPicker", () => {
  it("shows harness-specific models", async () => {
    const user = userEvent.setup();
    render(<ModelPicker harness="claude-cli" value="sonnet" onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /sonnet/i }));
    expect(screen.getByRole("option", { name: /opus/i })).toBeTruthy();
  });

  it("marks options offline when disconnected", () => {
    render(<ModelPicker harness="omp-rpc" value="omp-default" onChange={vi.fn()} offline />);
    expect(screen.getByRole("button", { name: /omp-default/i })).toBeTruthy();
  });

  it("calls onChange when model changes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ModelPicker harness="claude-cli" value="sonnet" onChange={onChange} />);
    await user.click(screen.getByRole("button", { name: /sonnet/i }));
    await user.click(screen.getByRole("option", { name: /opus/i }));
    expect(onChange).toHaveBeenCalledWith("opus");
  });
});
