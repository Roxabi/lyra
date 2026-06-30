import { useCallback, useState } from "react";

export type CardsTableView = "cards" | "table";

export const DESKTOP_VIEW_QUERY = "(min-width: 48rem)";

function storageKey(id: string) {
  return `factory.dashboard:view:${id}`;
}

function readStoredPreference<T extends string>(id: string, fallback: T, allowed: readonly T[]): T {
  if (typeof window === "undefined") return fallback;
  const stored = window.localStorage.getItem(storageKey(id));
  return (allowed as readonly string[]).includes(stored ?? "") ? (stored as T) : fallback;
}

/** Table on desktop (≥ md), cards on mobile — unless the user saved a preference. */
export function getResponsiveCardsTableDefault(): CardsTableView {
  if (typeof window === "undefined") return "table";
  if (typeof window.matchMedia !== "function") return "table";
  return window.matchMedia(DESKTOP_VIEW_QUERY).matches ? "table" : "cards";
}

export function useStoredPreference<T extends string>(
  id: string,
  defaultMode: T,
  allowed: readonly T[],
) {
  const [mode, setModeState] = useState<T>(() => readStoredPreference(id, defaultMode, allowed));

  const setMode = useCallback(
    (next: T) => {
      setModeState(next);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(storageKey(id), next);
      }
    },
    [id],
  );

  return [mode, setMode] as const;
}

export function useCardsTableViewPreference(id: string, defaultMode?: CardsTableView) {
  const fallback = defaultMode ?? getResponsiveCardsTableDefault();
  return useStoredPreference(id, fallback, ["cards", "table"] as const);
}