/**
 * TypeScript mirror of brand/tokens/*.css — SSOT for values is CSS.
 * Update when Forge tokens change in brand/tokens/colors.css.
 */

export const surface = {
  bg: "#0a0a0f",
  elevated: "#101018",
  card: "#18181f",
  cardHover: "#1f1f28",
} as const;

export const border = {
  base: "rgba(255,255,255,0.07)",
  hi: "#2a2a35",
} as const;

export const ink = {
  text: "#fafafa",
  muted: "#9ca3af",
  dim: "#7d8895",
} as const;

/** Forge Orange — single brand signal per composition. */
export const accent = {
  base: "#e85d04",
  hover: "#f97316",
  press: "#c2410c",
} as const;

/** Job/worker status — placeholder until #1772 vocabulary lands. */
export const status = {
  open: "#06b6d4",
  closing: "#f59e0b",
  error: "#f87171",
  idle: "#7d8895",
} as const;

export const fonts = {
  display: '"Outfit", system-ui, -apple-system, sans-serif',
  head: '"Outfit", system-ui, -apple-system, sans-serif',
  body: '"Inter", system-ui, -apple-system, "Segoe UI", sans-serif',
  mono: '"JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace',
} as const;

export type StatusKey = keyof typeof status;
