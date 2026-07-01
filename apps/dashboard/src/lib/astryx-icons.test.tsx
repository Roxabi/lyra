import { getIcon, getIconRegistry } from "@astryxdesign/core/Icon";
import { X } from "@phosphor-icons/react";
import { isValidElement, type ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { phosphorIconRegistry, registerAppIcons } from "./astryx-icons";

describe("registerAppIcons", () => {
  it("registers the app's Phosphor glyph for every Astryx semantic name", () => {
    // Falsifies if registerAppIcons is a no-op: getIconRegistry would return
    // Astryx's built-in fallback SVGs, not our phosphorIconRegistry elements.
    registerAppIcons();
    const registry = getIconRegistry();
    for (const name of Object.keys(phosphorIconRegistry) as (keyof typeof phosphorIconRegistry)[]) {
      expect(registry[name]).toBe(phosphorIconRegistry[name]);
    }
  });

  it("resolves a semantic name to the mapped Phosphor component, not a fallback", () => {
    registerAppIcons();
    const el = getIcon("close");
    expect(isValidElement(el)).toBe(true);
    expect((el as ReactElement).type).toBe(X);
  });
});
