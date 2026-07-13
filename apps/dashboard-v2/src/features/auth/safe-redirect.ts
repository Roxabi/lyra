const PUBLIC_AUTH_PATHS = new Set(["/login", "/accept-invite"]);

function hasControlOrBackslash(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 0x5c || c <= 0x1f || c === 0x7f) return true;
  }
  return false;
}

/** Same-origin path (+ optional search) only — blocks open redirects. */
export function safeRedirectPath(raw: unknown): string {
  if (typeof raw !== "string" || raw.length === 0) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/";
  if (hasControlOrBackslash(raw)) return "/";
  const pathOnly = raw.split(/[?#]/, 1)[0] ?? "/";
  if (PUBLIC_AUTH_PATHS.has(pathOnly)) return "/";
  return raw;
}
