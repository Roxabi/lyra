import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";

interface ShellTitleOverride {
  literal: string | null;
}

interface ShellTitleContextValue {
  override: ShellTitleOverride;
  setLiteral: (literal: string | null) => void;
}

const ShellTitleContext = createContext<ShellTitleContextValue | null>(null);

export function ShellTitleProvider({ children }: { children: ReactNode }) {
  const [override, setOverride] = useState<ShellTitleOverride>({ literal: null });

  const setLiteral = useCallback((literal: string | null) => {
    setOverride((prev) => (prev.literal === literal ? prev : { literal }));
  }, []);

  const value = useMemo(() => ({ override, setLiteral }), [override, setLiteral]);

  return <ShellTitleContext.Provider value={value}>{children}</ShellTitleContext.Provider>;
}

export function useShellTitle() {
  const ctx = useContext(ShellTitleContext);
  if (!ctx) throw new Error("useShellTitle must be used within ShellTitleProvider");
  return ctx;
}
