import { defineTheme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral";

/**
 * roxabi — Roxabi Factory brand theme for Astryx (slice 1, #2089).
 *
 * Single authority: every brand value below references a CSS custom property
 * declared in `brand/tokens/*.css` via `var(...)`, so the brand stylesheets
 * remain the sole source for color/radius/typography/motion. Nothing is
 * duplicated here — this file only *maps* brand tokens onto Astryx token names.
 *
 * Light/dark: brand color tokens (`--accent`, …) flip under the `[data-theme]`
 * selector in `brand/tokens/colors.css`. Astryx's root `<Theme mode>` writes
 * `data-theme` on `<html>` (see `src/main.tsx`), so a single `var(--accent)`
 * resolves to the correct mode automatically — no `[light, dark]` tuples needed.
 *
 * Extends `neutralTheme` to inherit its WCAG-checked categorical palette,
 * component refinements (badge/banner/switch/progressbar/card), typography
 * scale, and motion ratios; only brand-identity tokens are overridden here.
 *
 * Build: `bun run theme:build` → `src/astryx-theme/built/` (self-contained CSS +
 * `roxabi.js` module + `.d.ts`). Those built artifacts are the production inputs
 * consumed by `src/index.css` and `src/main.tsx`; a `theme_build_drift` CI gate
 * asserts they stay in sync with this source.
 */
export const roxabiTheme = defineTheme({
  name: "roxabi",
  extends: neutralTheme,

  tokens: {
    // Brand accent — Forge Orange (brand/tokens/colors.css `--accent` ramp).
    "--color-accent": "var(--accent)",
    "--color-accent-muted": "var(--accent-dim)",

    // Fonts — brand/tokens/typography.css (Inter body · Outfit heads · JetBrains mono).
    "--font-family-body": "var(--font-body)",
    "--font-family-heading": "var(--font-head)",
    "--font-family-code": "var(--font-mono)",

    // Radius — brand/tokens/spacing.css scale (4 · 8 · 12 · 20px).
    "--radius-inner": "var(--r-sm)",
    "--radius-element": "var(--r-md)",
    "--radius-container": "var(--r-lg)",
    "--radius-chat": "var(--r-lg)",
    "--radius-page": "var(--r-xl)",

    // Motion — brand/tokens/motion.css (base durations + easing curve).
    "--duration-fast": "var(--dur-micro)",
    "--duration-medium": "var(--dur-short)",
    "--duration-slow": "var(--dur-long)",
    "--ease-standard": "var(--ease-out)",
  },

  // No `icons` here on purpose (#2091): Astryx's <Theme> re-registers
  // `theme.icons` on every render, which would override the app's global
  // Phosphor registry. Icons are owned by `registerAppIcons()`
  // (src/lib/astryx-icons.tsx), called once at boot in src/main.tsx.
  //
  // ⚠ Inert ONLY because the app imports the *built* module (built/roxabi.js),
  // whose `icons` is derived by `astryx theme build`'s regex scan of THIS
  // source (no `icons:` key → none emitted). At RUNTIME, `defineTheme({extends:
  // neutralTheme})` still resolves `icons = neutralTheme.icons`, so importing
  // this .ts source directly (test / storybook / inline defineTheme) would
  // re-clobber the registry. Always consume `@/astryx-theme/built/roxabi`.
});
