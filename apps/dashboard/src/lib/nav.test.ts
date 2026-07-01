import { describe, expect, it } from "vitest";
import { isNavItemActive, resolveNavFlags, resolvePageTitle } from "@/lib/nav";

describe("nav", () => {
  it("resolves page titles from primary routes", () => {
    expect(resolvePageTitle("/")).toEqual({ key: "nav.overview" });
    expect(resolvePageTitle("/chat")).toEqual({ key: "nav.chat" });
    expect(resolvePageTitle("/jobs")).toEqual({ key: "nav.jobs" });
    expect(resolvePageTitle("/integrations")).toEqual({ key: "nav.integrations" });
    expect(resolvePageTitle("/ops")).toEqual({ key: "nav.ops" });
    expect(resolvePageTitle("/pipeline")).toEqual({ key: "nav.pipeline" });
    expect(resolvePageTitle("/spans")).toEqual({ key: "nav.spans" });
    expect(resolvePageTitle("/design-system")).toEqual({ key: "nav.designSystem" });
    expect(resolvePageTitle("/users")).toEqual({ key: "nav.users" });
  });

  it("marks chat and child paths active", () => {
    expect(isNavItemActive("/chat", { to: "/chat", labelKey: "nav.chat", Icon: {} as never })).toBe(
      true,
    );
    expect(
      isNavItemActive("/", { to: "/", labelKey: "nav.overview", Icon: {} as never, exact: true }),
    ).toBe(true);
    expect(
      isNavItemActive("/jobs", {
        to: "/",
        labelKey: "nav.overview",
        Icon: {} as never,
        exact: true,
      }),
    ).toBe(false);
  });

  it("resolves layout flags for chat full bleed", () => {
    expect(resolveNavFlags("/chat").fullBleed).toBe(true);
    expect(resolveNavFlags("/chat").hideBottomNav).toBe(true);
    expect(resolveNavFlags("/jobs").wideLayout).toBe(true);
    expect(resolveNavFlags("/integrations").wideLayout).toBe(true);
    expect(resolveNavFlags("/").wideLayout).toBe(true);
    expect(resolveNavFlags("/agents").hideBottomNav).toBe(false);
  });
});
