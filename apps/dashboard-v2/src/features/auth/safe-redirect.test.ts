import { describe, expect, it } from "vitest";
import { safeRedirectPath } from "./safe-redirect";

describe("safeRedirectPath", () => {
  it("allows same-origin absolute paths", () => {
    expect(safeRedirectPath("/")).toBe("/");
    expect(safeRedirectPath("/chat")).toBe("/chat");
    expect(safeRedirectPath("/agents?x=1")).toBe("/agents?x=1");
  });

  it("rejects open redirects and junk", () => {
    expect(safeRedirectPath("//evil.example")).toBe("/");
    expect(safeRedirectPath("https://evil.example")).toBe("/");
    expect(safeRedirectPath("http://evil.example")).toBe("/");
    expect(safeRedirectPath("javascript:alert(1)")).toBe("/");
    expect(safeRedirectPath("/\\evil.example")).toBe("/");
    expect(safeRedirectPath("")).toBe("/");
    expect(safeRedirectPath(null)).toBe("/");
    expect(safeRedirectPath(undefined)).toBe("/");
  });

  it("maps public auth paths to home", () => {
    expect(safeRedirectPath("/login")).toBe("/");
    expect(safeRedirectPath("/accept-invite")).toBe("/");
  });
});
