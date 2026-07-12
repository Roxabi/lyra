import { bffFetch } from "@/shared/api/bff-fetch";
import { parseBffResponse } from "@/shared/api/client";

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
  return bffFetch(input, init);
}

export async function fetchAdminAccess(): Promise<{ users: AdminUserAccess[] }> {
  const res = await adminFetch("/api/bff/admin/access");
  return parseBffResponse<{ users: AdminUserAccess[] }>(res);
}

export async function createAdminUser(body: AdminUserWriteBody): Promise<AdminUserAccess> {
  const res = await adminFetch("/api/bff/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseBffResponse<AdminUserAccess>(res);
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
  return parseBffResponse<AdminUserAccess>(res);
}
