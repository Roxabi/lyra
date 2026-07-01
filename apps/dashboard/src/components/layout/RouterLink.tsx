import { Link } from "@tanstack/react-router";
import { type ComponentProps, forwardRef } from "react";

/**
 * Adapts TanStack Router's `Link` to Astryx's polymorphic link contract.
 *
 * Astryx's `useLinkComponent` wraps any non-native link component passed to
 * `LinkProvider` so it receives both `href` and `to` (set to the same value),
 * enabling `to`-based routers (TanStack Router, React Router) to work without
 * a per-component adapter. TanStack Router's `Link` navigates via `to`, so
 * this component simply forwards the resolved destination through.
 */
export const RouterLink = forwardRef<
  HTMLAnchorElement,
  { href?: string; to?: string } & Record<string, unknown>
>(function RouterLink({ href, to, ...rest }, ref) {
  return (
    <Link ref={ref} to={(to ?? href ?? "#") as string} {...(rest as ComponentProps<typeof Link>)} />
  );
});
