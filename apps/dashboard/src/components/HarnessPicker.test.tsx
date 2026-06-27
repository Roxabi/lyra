import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HarnessPicker } from "@/components/HarnessPicker";

describe("HarnessPicker", () => {
  it("lists Clipool and OMP options", () => {
    render(<HarnessPicker value="claude-cli" onChange={vi.fn()} />);
    expect(screen.getByLabelText("Harness")).toBeTruthy();
    expect(screen.getByRole("option", { name: "Clipool" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "OMP" })).toBeTruthy();
  });

  it("calls onChange when selection changes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<HarnessPicker value="claude-cli" onChange={onChange} />);
    await user.selectOptions(screen.getByLabelText("Harness"), "omp-rpc");
    expect(onChange).toHaveBeenCalledWith("omp-rpc");
  });
});
