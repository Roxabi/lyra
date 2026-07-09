import type { ReactNode } from "react";

/** Context line under the shell header — not a duplicate page title. */
export function PageIntro({ children }: { children: ReactNode }) {
  return <p className="mb-6 text-sm text-muted-foreground">{children}</p>;
}
