import { Divider } from "@astryxdesign/core/Divider";
import type { ComponentProps } from "react";

/**
 * Separator — thin wrapper over Astryx Divider (slice 0, #2088). Keeps the
 * `@/components/ui/separator` import path stable for existing consumers.
 *
 * A11y delta from the former Radix wrapper: that wrapper defaulted
 * `decorative=true` → Radix `role="none"` (hidden from the accessibility tree,
 * no `aria-orientation`). Divider always renders `<div role="separator"
 * aria-orientation>` — separators are now exposed to assistive tech. For a
 * purely decorative divider, pass `aria-hidden` through `...props`.
 */
function Separator(props: ComponentProps<typeof Divider>) {
  return <Divider {...props} />;
}

export { Separator };
