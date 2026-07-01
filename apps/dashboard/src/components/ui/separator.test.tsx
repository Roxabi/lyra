import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Separator } from "./separator";

describe("Separator (Astryx Divider)", () => {
  it("renders a separator element (smoke)", () => {
    render(<Separator />);
    expect(screen.getByRole("separator").getAttribute("aria-orientation")).toBe("horizontal");
  });

  it("forwards orientation to the underlying Divider", () => {
    render(<Separator orientation="vertical" />);
    expect(screen.getByRole("separator").getAttribute("aria-orientation")).toBe("vertical");
  });

  it("passes through className and rest props to the root element", () => {
    render(<Separator className="my-sep" data-testid="sep" />);
    const sep = screen.getByTestId("sep");
    expect(sep.getAttribute("role")).toBe("separator");
    expect(sep.className).toContain("my-sep");
  });
});
