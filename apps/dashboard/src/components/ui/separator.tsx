import { Divider } from "@astryxdesign/core/Divider";
import type { ComponentProps } from "react";

/**
 * Separator — thin wrapper over Astryx Divider (slice 0, #2088). Keeps the
 * `@/components/ui/separator` import path stable for existing consumers; Divider
 * renders `<div role="separator" aria-orientation>` like the former Radix impl.
 */
function Separator({ orientation = "horizontal", ...props }: ComponentProps<typeof Divider>) {
  return <Divider orientation={orientation} {...props} />;
}

export { Separator };
