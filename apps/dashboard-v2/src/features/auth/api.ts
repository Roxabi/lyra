import { bffFetch, bffJson } from "@/shared/api/bff-fetch";
import { BffApiError, readBffErrorDetail } from "@/shared/api/client";

export { getActiveOrgId, setActiveOrgId } from "@/shared/api/org-context";

export type AuthUser = {
  id: string;
  email: string | null;
  display_name: string | null;
  global_role: string;
  status: string;
};

export type AuthPrincipal = {
  user_id: string;
  roles: string[];
  org_ids: string[];
  active_org_id: string | null;
  via: string;
};

export type AuthSession = {
  user: AuthUser | null;
  principal: AuthPrincipal;
};

export type OrgRow = {
  id: string;
  name: string;
  created_by: string;
  created_at: string;
};

export type LinkStatus = {
  user_id: string;
  links: { platform: string; platform_key: string; linked_at: string }[];
  chat_ready: boolean;
  required: string[];
};

/** @deprecated Use bffFetch — kept as alias for call sites. */
export const authFetch = bffFetch;
/** @deprecated Use bffJson — kept as alias for call sites. */
export const authJson = bffJson;

export async function fetchMe(): Promise<AuthSession | null> {
  const res = await bffFetch("/api/bff/auth/me");
  if (res.status === 401) return null;
  // 503 (CP unwired) is not an admin free-pass — treat as unauthenticated.
  if (res.status === 503) return null;
  if (!res.ok) {
    throw new BffApiError(res.status, await readBffErrorDetail(res));
  }
  return res.json() as Promise<AuthSession>;
}

export async function login(email: string, password: string): Promise<AuthSession> {
  return bffJson<AuthSession>("/api/bff/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export async function logout(): Promise<void> {
  await bffFetch("/api/bff/auth/logout", { method: "POST" });
}

export async function acceptInvite(body: {
  token: string;
  password: string;
  display_name?: string;
}): Promise<AuthSession> {
  return bffJson<AuthSession>("/api/bff/auth/accept-invite", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchOrgs(): Promise<{ orgs: OrgRow[]; active_org_id: string | null }> {
  return bffJson("/api/bff/orgs");
}

export async function createOrg(name: string): Promise<{ org: OrgRow }> {
  return bffJson("/api/bff/orgs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function fetchLinkStatus(): Promise<LinkStatus> {
  return bffJson("/api/bff/auth/links");
}

export async function createLinkCode(
  platform?: string | null,
): Promise<{ token: string; instructions: string; platform: string | null }> {
  return bffJson("/api/bff/auth/links/code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ platform: platform ?? null }),
  });
}

export async function unlinkPlatform(platform: string): Promise<void> {
  await bffJson(`/api/bff/auth/links/${platform}`, { method: "DELETE" });
}
