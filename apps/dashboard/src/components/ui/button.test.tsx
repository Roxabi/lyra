import { Badge } from "@astryxdesign/core/Badge";
import { LinkProvider } from "@astryxdesign/core/Link";
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

  it("maps shadcn variants to Astryx variants (data-variant)", () => {
    const { rerender } = render(<Button variant="brand">x</Button>);
    expect(screen.getByRole("button").getAttribute("data-variant")).toBe("primary");
    rerender(<Button variant="outline">x</Button>);
    expect(screen.getByRole("button").getAttribute("data-variant")).toBe("ghost");
    rerender(<Button variant="destructive">x</Button>);
    expect(screen.getByRole("button").getAttribute("data-variant")).toBe("destructive");
  });

  it("renders an external native anchor with target/rel when as='a'", () => {
    render(
      <Button as="a" href="https://example.com" target="_blank" rel="noopener noreferrer">
        External
      </Button>,
    );
    const link = screen.getByRole("link", { name: "External" });
    expect(link.getAttribute("href")).toBe("https://example.com");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("fires onClick on a link-mode (href) button", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button href="/agents/lyra" onClick={onClick}>
        Edit
      </Button>,
    );
    await user.click(screen.getByRole("link", { name: "Edit" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("resolves href through a LinkProvider component (router path)", () => {
    const StubLink = vi.fn((props: { to?: string; href?: string; children?: React.ReactNode }) => (
      <a data-testid="stub" href={props.to ?? props.href}>
        {props.children}
      </a>
    ));
    render(
      <LinkProvider component={StubLink}>
        <Button href="/jobs">Go</Button>
      </LinkProvider>,
    );
    // Astryx forwards `to={href}` to the LinkProvider component.
    expect(StubLink).toHaveBeenCalled();
    expect(screen.getByTestId("stub").getAttribute("href")).toBe("/jobs");
  });

  it("derives the accessible name from a nested Badge's label prop", () => {
    render(
      <Button>
        <div>
          <Badge variant="neutral" label="TG" />
          <span>hello</span>
        </div>
      </Button>,
    );
    // extractText reads Badge's `label` prop + space-joins → "TG hello", not "hello".
    expect(screen.getByRole("button", { name: "TG hello" })).toBeTruthy();
  });
});
