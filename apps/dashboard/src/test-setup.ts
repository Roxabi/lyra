import "@/i18n";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { registerAppIcons } from "@/lib/astryx-icons";

// Register the app's Phosphor glyphs into Astryx's global icon registry so
// component tests render the real icon set (as main.tsx does at boot) rather
// than Astryx's built-in fallback SVGs (#2091).
registerAppIcons();

// jsdom lacks matchMedia + ResizeObserver, which Astryx's responsive shell
// (AppShell/SideNav) reads on mount. Polyfill them (desktop default: no match).
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

afterEach(() => {
  cleanup();
});
