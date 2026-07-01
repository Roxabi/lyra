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

// jsdom doesn't implement scrollTo; TanStack Router calls it during scroll
// restoration on navigation, which would spew "Not implemented" noise in tests
// that route (e.g. UserMenu close-on-navigate). No-op it.
window.scrollTo = () => {};

// jsdom (26) doesn't implement the native Popover API that Astryx's layer
// primitives (Popover/DropdownMenu/Tooltip) call via showPopover()/hidePopover()
// in useLayer. Polyfill as no-ops so opening a layer doesn't throw in unit
// tests. jsdom applies no UA `[popover]{display:none}` styling, so layer content
// is ALWAYS present in the test DOM regardless of open state.
//
// ⚠ Test-authoring hazard: because layer content is always mounted, DO NOT
// assert open/close via DOM presence (`queryByText(...).not.toBeInTheDocument()`
// then `.toBeInTheDocument()`) — that pattern passes even if the trigger's click
// handler is broken (a tautology). Assert the click-dependent signal instead:
// the trigger's `aria-expanded` flips "false"→"true" (Astryx sets it
// imperatively). Also note layer content can duplicate shell labels (e.g. nav
// entries) → scope full-shell queries with `within()` to avoid multiple-match
// errors. True open/close visibility is covered by e2e.
if (!HTMLElement.prototype.showPopover) {
  HTMLElement.prototype.showPopover = () => {};
  HTMLElement.prototype.hidePopover = () => {};
  HTMLElement.prototype.togglePopover = () => true;
}

afterEach(() => {
  cleanup();
});
