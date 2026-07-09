import { LayoutDashboard } from "lucide-react";
import { describe, expect, it } from "vitest";
import { type AppNavItem, isNavItemActive, resolveLayoutFlags, resolvePageTitle } from "@/app/nav";

const homeItem: AppNavItem = {
  to: "/",
  labelKey: "nav.overview",
  Icon: LayoutDashboard,
  exact: true,
  status: "ready",
};

const agentsItem: AppNavItem = {
  to: "/agents",
  labelKey: "nav.agents",
  Icon: LayoutDashboard,
  status: "planned",
};

describe("isNavItemActive", () => {
  it("matches exact home route", () => {
    expect(isNavItemActive("/", homeItem)).toBe(true);
    expect(isNavItemActive("/chat", homeItem)).toBe(false);
  });

  it("matches nested agent detail routes", () => {
    expect(isNavItemActive("/agents/lyra", agentsItem)).toBe(true);
  });
});

describe("resolvePageTitle", () => {
  it("returns nav labelKey for known paths", () => {
    expect(resolvePageTitle("/design-system")).toEqual({ key: "nav.designSystem" });
    expect(resolvePageTitle("/agents/lyra")).toEqual({ key: "nav.agents" });
    expect(resolvePageTitle("/")).toEqual({ key: "nav.overview" });
    expect(resolvePageTitle("/chat")).toEqual({ key: "nav.chat" });
    expect(resolvePageTitle("/jobs")).toEqual({ key: "nav.jobs" });
    expect(resolvePageTitle("/integrations")).toEqual({ key: "nav.integrations" });
    expect(resolvePageTitle("/ops")).toEqual({ key: "nav.ops" });
    expect(resolvePageTitle("/pipeline")).toEqual({ key: "nav.pipeline" });
    expect(resolvePageTitle("/spans")).toEqual({ key: "nav.spans" });
    expect(resolvePageTitle("/users")).toEqual({ key: "nav.users" });
  });
});

describe("resolveLayoutFlags", () => {
  it("returns fullBleed for chat", () => {
    expect(resolveLayoutFlags("/chat")).toEqual({ fullBleed: true, wide: false });
  });

  it("returns wide for agents list", () => {
    expect(resolveLayoutFlags("/agents")).toEqual({ fullBleed: false, wide: true });
  });
});
