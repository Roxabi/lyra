import { LayoutDashboard } from "lucide-react";
import { describe, expect, it } from "vitest";
import { type AppNavItem, isNavItemActive, resolveLayoutFlags, resolvePageLabel } from "@/app/nav";

const homeItem: AppNavItem = {
  to: "/",
  label: "Overview",
  Icon: LayoutDashboard,
  exact: true,
  status: "ready",
};

const agentsItem: AppNavItem = {
  to: "/agents",
  label: "Agents",
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

describe("resolvePageLabel", () => {
  it("returns nav label for known paths", () => {
    expect(resolvePageLabel("/design-system")).toBe("Design system");
    expect(resolvePageLabel("/agents/lyra")).toBe("Agents");
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
