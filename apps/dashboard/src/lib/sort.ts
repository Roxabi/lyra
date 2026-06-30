export type SortDirection = "asc" | "desc";

export function toggleSort<T extends string>(
  currentKey: T,
  currentDir: SortDirection,
  nextKey: T,
): { key: T; direction: SortDirection } {
  if (currentKey !== nextKey) return { key: nextKey, direction: "asc" };
  return { key: nextKey, direction: currentDir === "asc" ? "desc" : "asc" };
}

export function compareStrings(a: string, b: string, direction: SortDirection): number {
  const cmp = a.localeCompare(b, undefined, { sensitivity: "base" });
  return direction === "asc" ? cmp : -cmp;
}

export function compareNumbers(
  a: number | null | undefined,
  b: number | null | undefined,
  direction: SortDirection,
): number {
  const av = a ?? -1;
  const bv = b ?? -1;
  return direction === "asc" ? av - bv : bv - av;
}