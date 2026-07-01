import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FilterChip } from "@/components/ui/filter-chip";

describe("FilterChip", () => {
  it("reflects active state as aria-pressed on the Astryx ToggleButton", () => {
    const { rerender } = render(
      <FilterChip active onClick={vi.fn()}>
        open
      </FilterChip>,
    );
    const button = screen.getByRole("button", { name: "open" });
    expect(button.getAttribute("aria-pressed")).toBe("true");

    rerender(
      <FilterChip active={false} onClick={vi.fn()}>
        open
      </FilterChip>,
    );
    expect(screen.getByRole("button", { name: "open" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("calls onClick when toggled", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <FilterChip active={false} onClick={onClick}>
        closing
      </FilterChip>,
    );
    await user.click(screen.getByRole("button", { name: "closing" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
