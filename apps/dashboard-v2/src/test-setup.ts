import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import i18n from "@/i18n";

void i18n.changeLanguage("en");

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

window.scrollTo = () => {};

afterEach(() => {
  cleanup();
});
