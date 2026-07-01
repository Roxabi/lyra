import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HarnessPicker } from "@/components/HarnessPicker";

describe("HarnessPicker", () => {
  it("lists Clipool and OMP options", async () => {
    const user = userEvent.setup();
    render(<HarnessPicker value="claude-cli" onChange={vi.fn()} />);
    const combobox = screen.getByRole("combobox");
    expect(combobox.textContent).toContain("Clipool");
    expect(combobox.getAttribute("aria-expanded")).toBe("false");
    await user.click(combobox);
    expect(combobox.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("option", { name: /OMP/i })).toBeTruthy();
  });

  it("shows override hint when value differs from DB default", () => {
    render(<HarnessPicker value="omp-rpc" dbDefault="claude-cli" onChange={vi.fn()} />);
    expect(screen.getByText(/≠ défaut DB/)).toBeTruthy();
  });

  it("calls onChange when selection changes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<HarnessPicker value="claude-cli" onChange={onChange} />);
    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: /OMP/i }));
    expect(onChange).toHaveBeenCalledWith("omp-rpc");
  });
});
