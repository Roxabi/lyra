import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HarnessPicker } from "@/components/HarnessPicker";

describe("HarnessPicker", () => {
  it("lists Clipool and OMP options", async () => {
    const user = userEvent.setup();
    render(<HarnessPicker value="claude-cli" onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Clipool/i }));
    expect(screen.getByRole("option", { name: /OMP/i })).toBeTruthy();
  });

  it("shows override hint when value differs from DB default", () => {
    render(<HarnessPicker value="omp-rpc" dbDefault="claude-cli" onChange={vi.fn()} />);
    expect(screen.getByText(/≠ DB default/)).toBeTruthy();
  });

  it("calls onChange when selection changes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<HarnessPicker value="claude-cli" onChange={onChange} />);
    await user.click(screen.getByRole("button", { name: /Clipool/i }));
    await user.click(screen.getByRole("option", { name: /OMP/i }));
    expect(onChange).toHaveBeenCalledWith("omp-rpc");
  });
});
