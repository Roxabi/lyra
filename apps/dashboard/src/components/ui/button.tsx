import { Button as AstryxButton, type ButtonVariant } from "@astryxdesign/core/Button";
import { isValidElement, type MouseEventHandler, type ReactNode } from "react";

/**
 * Astryx-native Button. Composes `@astryxdesign/core/Button` behind the app's
 * long-standing `{ variant, size, loading, ... }` API so the ~14 call-sites
 * change minimally. Internals are 100% Astryx (no shadcn/Radix Slot).
 *
 * The former shadcn `asChild` + `<Link>` pattern is replaced by Astryx Button's
 * native link mode: pass `href` for router links (resolved through the app's
 * `LinkProvider` → TanStack Router) or `as="a"` + `href` for external links
 * (rendered as a native anchor, bypassing the router).
 */

// shadcn variant → Astryx variant. Astryx `primary` reads the brand accent in
// the roxabi theme, so `default`/`brand` both map to it; `outline` → `secondary`.
const VARIANT_MAP = {
  default: "primary",
  brand: "primary",
  secondary: "secondary",
  outline: "secondary",
  ghost: "ghost",
  destructive: "destructive",
} as const satisfies Record<string, ButtonVariant>;

const SIZE_MAP = { default: "md", sm: "sm", lg: "lg" } as const;

/** Concatenate the visible text of a ReactNode tree — used as the accessible name. */
function extractText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (isValidElement(node)) return extractText((node.props as { children?: ReactNode }).children);
  return "";
}

export interface ButtonProps {
  variant?: keyof typeof VARIANT_MAP;
  size?: "default" | "sm" | "lg" | "icon";
  loading?: boolean;
  disabled?: boolean;
  type?: "button" | "submit" | "reset";
  onClick?: MouseEventHandler<HTMLButtonElement>;
  className?: string;
  /** Router link target (resolved via LinkProvider). Use with a concrete path. */
  href?: string;
  /** Force a native anchor (external links) — pass `as="a"` + `href`. */
  as?: "a";
  target?: string;
  rel?: string;
  /** Hover/focus tooltip — maps to Astryx Button's `tooltip`. */
  title?: string;
  "aria-label"?: string;
  children?: ReactNode;
}

export function Button({
  variant = "default",
  size = "default",
  loading,
  disabled,
  type = "button",
  onClick,
  className,
  href,
  as,
  target,
  rel,
  title,
  "aria-label": ariaLabel,
  children,
}: ButtonProps) {
  const isIconOnly = size === "icon";
  const label = ariaLabel ?? extractText(children);
  const common = {
    label,
    variant: VARIANT_MAP[variant],
    size: isIconOnly ? ("md" as const) : SIZE_MAP[size],
    isLoading: loading,
    isDisabled: disabled,
    type,
    onClick,
    className,
    href,
    as,
    target,
    rel,
    tooltip: title,
  };

  // Icon-only: the child is the icon; `label` (from aria-label) is the a11y name.
  if (isIconOnly) {
    return <AstryxButton {...common} icon={children} isIconOnly />;
  }
  // Plain-string child renders via `label` (no redundant aria); rich children
  // render as visible content with `label` serving as the accessible name.
  if (typeof children === "string") {
    return <AstryxButton {...common} />;
  }
  return <AstryxButton {...common}>{children}</AstryxButton>;
}
