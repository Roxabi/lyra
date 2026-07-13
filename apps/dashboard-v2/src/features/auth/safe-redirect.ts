/** Same-origin path only — blocks open redirects (`//evil`, absolute URLs). */
export function safeRedirectPath(raw: unknown): string {
  if (typeof raw !== "string" || raw.length === 0) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}
