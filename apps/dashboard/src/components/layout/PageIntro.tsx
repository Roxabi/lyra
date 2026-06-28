import type { ReactNode } from "react";

interface PageIntroProps {
  children: ReactNode;
}

/** Context line under the shell header — not a duplicate page title. */
export function PageIntro({ children }: PageIntroProps) {
  return <p className="mb-6 text-sm text-muted-foreground">{children}</p>;
}