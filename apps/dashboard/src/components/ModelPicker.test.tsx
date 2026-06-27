import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ModelPicker } from "@/components/ModelPicker";

describe("ModelPicker", () => {
  it("shows harness-specific models", () => {
    render(<ModelPicker harness="claude-cli" value="sonnet" onChange={vi.fn()} />);
    expect(screen.getByLabelText("Model")).toBeTruthy();
    expect(screen.getByRole("option", { name: "sonnet" })).toBeTruthy();
  });

  it("marks options offline when disconnected", () => {
    render(<ModelPicker harness="omp-rpc" value="omp-default" onChange={vi.fn()} offline />);
    expect(screen.getByRole("option", { name: "omp-default (Hors ligne)" })).toBeTruthy();
  });

  it("calls onChange when model changes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ModelPicker harness="claude-cli" value="sonnet" onChange={onChange} />);
    await user.selectOptions(screen.getByLabelText("Model"), "opus");
    expect(onChange).toHaveBeenCalledWith("opus");
  });
});
