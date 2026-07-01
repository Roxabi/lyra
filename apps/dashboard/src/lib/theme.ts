/**
 * Dark/light preference — the STATE source of truth (persisted preference +
 * change event). It deliberately does NOT touch the DOM: `<html>`'s `data-theme`
 * is written by the Astryx `<Theme mode>` provider in `src/main.tsx` after mount
 * (and by a one-shot bootstrap in `index.html` pre-mount), which mirrors this
 * preference via `useTheme()`. `color-scheme` follows from the `[data-theme]`
 * rules in `brand/tokens/colors.css`. Keeping the DOM writes out of this module
 * removes the `data-theme` dual-ownership it had before (#2089).
 */

export type Theme = "dark" | "light";

export const THEME_CHANGE_EVENT = "factory-dashboard:theme-change";

/** localStorage key — MUST stay in sync with the bootstrap script in index.html. */
export const THEME_STORAGE_KEY = "factory-dashboard:theme";

export function readTheme(): Theme {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

/**
 * Persist the preference and notify consumers. Does not write the DOM — the
 * Astryx `<Theme mode>` provider reflects the change to `<html>`.
 */
export function applyTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // ignore — storage unavailable (private mode)
  }
  window.dispatchEvent(new CustomEvent(THEME_CHANGE_EVENT, { detail: theme }));
}

export function toggleTheme(): Theme {
  const next: Theme = readTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  return next;
}
