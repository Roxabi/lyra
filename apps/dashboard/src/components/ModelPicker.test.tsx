import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ModelPicker } from "@/components/ModelPicker";

describe("ModelPicker", () => {
  it("shows harness-specific models", async () => {
    const user = userEvent.setup();
    render(<ModelPicker harness="claude-cli" value="sonnet" onChange={vi.fn()} />);
    const combobox = screen.getByRole("combobox");
    expect(combobox.textContent).toContain("sonnet");
    expect(combobox.getAttribute("aria-expanded")).toBe("false");
    await user.click(combobox);
    expect(combobox.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("option", { name: /opus/i })).toBeTruthy();
  });

  it("marks options offline when disconnected", () => {
    render(<ModelPicker harness="omp-rpc" value="omp-default" onChange={vi.fn()} offline />);
    expect(screen.getByRole("combobox").textContent).toContain("omp-default");
  });

  it("calls onChange when model changes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ModelPicker harness="claude-cli" value="sonnet" onChange={onChange} />);
    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: /opus/i }));
    expect(onChange).toHaveBeenCalledWith("opus");
  });
});
