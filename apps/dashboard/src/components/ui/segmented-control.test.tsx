import { List, SquaresFour } from "@phosphor-icons/react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SegmentedControl } from "@/components/ui/segmented-control";

const OPTIONS = [
  { value: "cards" as const, label: "Cards", icon: SquaresFour },
  { value: "table" as const, label: "Table", icon: List },
];

describe("SegmentedControl", () => {
  it("renders Astryx radio-group semantics with the active option checked", () => {
    render(
      <SegmentedControl options={OPTIONS} value="cards" onChange={vi.fn()} ariaLabel="View mode" />,
    );
    const active = screen.getByRole("radio", { name: /Cards/i });
    expect(active.getAttribute("aria-checked")).toBe("true");
    const inactive = screen.getByRole("radio", { name: /Table/i });
    expect(inactive.getAttribute("aria-checked")).toBe("false");
  });

  it("calls onChange with the selected value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <SegmentedControl
        options={OPTIONS}
        value="cards"
        onChange={onChange}
        ariaLabel="View mode"
      />,
    );
    await user.click(screen.getByRole("radio", { name: /Table/i }));
    expect(onChange).toHaveBeenCalledWith("table");
  });

  it("keeps the label as accessible name when responsive collapses to icons", () => {
    // jsdom matchMedia resolves to false → narrow viewport → icon-only, label
    // surfaces as aria-label so the option stays queryable by name.
    render(
      <SegmentedControl
        options={OPTIONS}
        value="cards"
        onChange={vi.fn()}
        ariaLabel="View mode"
        compact="responsive"
      />,
    );
    expect(screen.getByRole("radio", { name: /Table/i })).toBeTruthy();
  });
});
