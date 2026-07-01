import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "factory.dashboard.sidebar-collapsed";

function persist(collapsed: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // ignore — persistence is best-effort
  }
}

export function useSidebarCollapsed() {
  const [collapsed, setCollapsedState] = useState(false);

  useEffect(() => {
    try {
      setCollapsedState(localStorage.getItem(STORAGE_KEY) === "1");
    } catch {
      setCollapsedState(false);
    }
  }, []);

  // Persisting setter — the single write path for the collapsed preference, so
  // consumers (e.g. Astryx SideNav's `onCollapsedChange`) don't re-implement it.
  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedState(next);
    persist(next);
  }, []);

  const toggle = useCallback(() => {
    setCollapsedState((prev) => {
      const next = !prev;
      persist(next);
      return next;
    });
  }, []);

  return { collapsed, toggle, setCollapsed };
}
