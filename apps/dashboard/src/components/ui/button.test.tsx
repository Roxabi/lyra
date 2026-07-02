import { CircleNotch } from "@phosphor-icons/react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "@/components/ui/button";

describe("Button (Astryx adapter)", () => {
  it("renders a string child as the button's accessible name", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  it("fires onClick", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    await user.click(screen.getByRole("button", { name: "Go" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disables while loading", () => {
    render(
      <Button loading aria-label="Saving">
        <CircleNotch />
      </Button>,
    );
    expect((screen.getByRole("button", { name: "Saving" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("uses aria-label as the accessible name for an icon-only button", () => {
    render(
      <Button size="icon" aria-label="Toggle theme">
        <CircleNotch />
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Toggle theme" })).toBeTruthy();
  });

  it("renders a link when given href (native anchor without a LinkProvider)", () => {
    render(<Button href="/jobs">View jobs</Button>);
    const link = screen.getByRole("link", { name: "View jobs" });
    expect(link.getAttribute("href")).toBe("/jobs");
  });
});
