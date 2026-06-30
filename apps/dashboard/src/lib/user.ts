import type { OperatorProfile } from "@/lib/operator-profile";

export function userDisplayName(user: Pick<OperatorProfile, "name" | "email">): string {
  const trimmed = user.name.trim();
  return trimmed.length > 0 ? trimmed : user.email;
}

export function userInitials(user: Pick<OperatorProfile, "name" | "email">): string {
  const trimmed = user.name.trim();
  if (trimmed) {
    const parts = trimmed.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) {
      return `${parts[0]?.[0] ?? ""}${parts[parts.length - 1]?.[0] ?? ""}`.toUpperCase();
    }
    return trimmed.slice(0, 2).toUpperCase();
  }
  const local = user.email.split("@")[0] ?? user.email;
  return local.slice(0, 2).toUpperCase();
}