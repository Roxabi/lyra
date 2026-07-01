import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PopoverSelect } from "@/components/ui/popover-select";

const OPTIONS = [
  { value: "a", label: "Option A", hint: "with hint" },
  { value: "b", label: "Option B" },
  { value: "c", label: "Option C", disabled: true },
];

describe("PopoverSelect", () => {
  it("exposes the label as the combobox accessible name", () => {
    render(<PopoverSelect label="Choose" value="a" options={OPTIONS} onChange={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Choose" })).toBeTruthy();
  });

  it("renders each option and the per-option hint", () => {
    render(<PopoverSelect label="Choose" value="a" options={OPTIONS} onChange={vi.fn()} />);
    expect(screen.getByRole("option", { name: /Option B/i })).toBeTruthy();
    expect(screen.getByText("with hint")).toBeTruthy();
  });

  it("calls onChange with the chosen value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<PopoverSelect label="Choose" value="a" options={OPTIONS} onChange={onChange} />);
    await user.click(screen.getByRole("combobox", { name: "Choose" }));
    await user.click(screen.getByRole("option", { name: /Option B/i }));
    expect(onChange).toHaveBeenCalledWith("b");
  });
});
