import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Vite `?raw` import (typed via vite/client) — read the built HTML as a string
// without pulling in @types/node just for this one assertion.
import indexHtml from "../../index.html?raw";
import { applyTheme, readTheme, THEME_CHANGE_EVENT, THEME_STORAGE_KEY, toggleTheme } from "./theme";

const html = document.documentElement;

beforeEach(() => {
  localStorage.clear();
  html.removeAttribute("data-theme");
  html.style.removeProperty("color-scheme");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("applyTheme — state-only contract (#2089)", () => {
  it("persists the preference under THEME_STORAGE_KEY", () => {
    applyTheme("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("dispatches THEME_CHANGE_EVENT with the new theme in detail", () => {
    const detail = vi.fn();
    const listener = (e: Event) => detail((e as CustomEvent<string>).detail);
    window.addEventListener(THEME_CHANGE_EVENT, listener);
    applyTheme("light");
    window.removeEventListener(THEME_CHANGE_EVENT, listener);
    expect(detail).toHaveBeenCalledWith("light");
  });

  it("does NOT write data-theme to <html> — that ownership belongs to Astryx <Theme>", () => {
    // Regression guard: re-adding a DOM write here recreates the dual-ownership
    // this slice removed. Astryx <Theme mode> is the sole runtime writer.
    applyTheme("light");
    expect(html.getAttribute("data-theme")).toBeNull();
  });

  it("does NOT set an inline color-scheme (CSS [data-theme] rules drive it)", () => {
    // An inline style.colorScheme would outrank the stylesheet rules and freeze
    // color-scheme on first paint, so applyTheme must not touch it.
    applyTheme("light");
    expect(html.style.colorScheme).toBe("");
  });

  it("still persists when localStorage.setItem throws (private mode)", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const detail = vi.fn();
    const listener = (e: Event) => detail((e as CustomEvent<string>).detail);
    window.addEventListener(THEME_CHANGE_EVENT, listener);
    expect(() => applyTheme("dark")).not.toThrow();
    window.removeEventListener(THEME_CHANGE_EVENT, listener);
    expect(detail).toHaveBeenCalledWith("dark"); // event still fires
  });
});

describe("readTheme", () => {
  it("returns the stored 'light' preference", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    expect(readTheme()).toBe("light");
  });

  it("defaults to 'dark' when unset or unrecognized", () => {
    expect(readTheme()).toBe("dark");
    localStorage.setItem(THEME_STORAGE_KEY, "banana");
    expect(readTheme()).toBe("dark");
  });

  it("falls back to 'dark' when localStorage.getItem throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(readTheme()).toBe("dark");
  });
});

describe("toggleTheme", () => {
  it("flips the persisted preference and returns the next value", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    expect(toggleTheme()).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(toggleTheme()).toBe("dark");
  });
});

describe("FOUC bootstrap ↔ THEME_STORAGE_KEY sync", () => {
  it("the index.html pre-paint script reads the same storage key", () => {
    // The inline bootstrap can't import THEME_STORAGE_KEY (it runs before the
    // module graph), so it re-literals the key. Guard the two stay identical —
    // a rename here fails loudly instead of silently breaking the FOUC guard.
    expect(indexHtml).toContain(`localStorage.getItem("${THEME_STORAGE_KEY}")`);
  });
});
