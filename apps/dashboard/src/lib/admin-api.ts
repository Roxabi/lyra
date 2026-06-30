import { operatorAuthHeaders } from "@/lib/operator-auth";

export interface AdminPlatformIdentity {
  platform: string;
  platform_uid: string;
  platform_key: string;
}

export interface AdminUserAccess {
  user_id: string;
  display_name: string | null;
  email: string | null;
  telegram: AdminPlatformIdentity | null;
  discord: AdminPlatformIdentity | null;
  agents: string[];
}

export interface AdminUserWriteBody {
  display_name: string;
  email: string;
  telegram_uid?: string | null;
  discord_uid?: string | null;
  agents?: string[];
}

async function adminFetch(input: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const auth = operatorAuthHeaders();
  for (const [key, value] of Object.entries(auth)) {
    headers.set(key, value as string);
  }
  return fetch(input, { ...init, headers });
}

export async function fetchAdminAccess(): Promise<{ users: AdminUserAccess[] }> {
  const res = await adminFetch("/api/bff/admin/access");
  if (!res.ok) throw new Error("admin access failed");
  return res.json() as Promise<{ users: AdminUserAccess[] }>;
}

export async function createAdminUser(body: AdminUserWriteBody): Promise<AdminUserAccess> {
  const res = await adminFetch("/api/bff/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("admin user create failed");
  return res.json() as Promise<AdminUserAccess>;
}

export async function patchAdminUser(
  userId: string,
  body: Partial<AdminUserWriteBody> & {
    telegram_uid?: string | null;
    discord_uid?: string | null;
    agents?: string[];
  },
): Promise<AdminUserAccess> {
  const res = await adminFetch(`/api/bff/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("admin user patch failed");
  return res.json() as Promise<AdminUserAccess>;
}
