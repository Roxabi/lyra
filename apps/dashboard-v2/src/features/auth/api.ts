import { BffApiError, parseBffResponse, readBffErrorDetail } from "@/shared/api/client";
import { operatorAuthHeaders } from "@/shared/api/operator-auth";

const ORG_KEY = "factory.dashboard.activeOrgId";

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

export function getActiveOrgId(): string | null {
  try {
    return localStorage.getItem(ORG_KEY);
  } catch {
    return null;
  }
}

export function setActiveOrgId(orgId: string | null): void {
  try {
    if (orgId) localStorage.setItem(ORG_KEY, orgId);
    else localStorage.removeItem(ORG_KEY);
  } catch {
    // private mode
  }
}

function sessionHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const auth = operatorAuthHeaders();
  for (const [k, v] of Object.entries(auth)) {
    if (typeof v === "string") headers.set(k, v);
  }
  const org = getActiveOrgId();
  if (org) headers.set("X-Factory-Org-Id", org);
  return headers;
}

/** Cookie session + optional API key / org header. Always credentials:include. */
export async function authFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = sessionHeaders(init?.headers);
  return fetch(path, { ...init, headers, credentials: "include" });
}

export async function authJson<T>(path: string, init?: RequestInit): Promise<T> {
  return parseBffResponse<T>(await authFetch(path, init));
}

export async function fetchMe(): Promise<AuthSession | null> {
  const res = await authFetch("/api/bff/auth/me");
  if (res.status === 401) return null;
  if (res.status === 503) {
    // Control-plane not wired — open console (legacy Tailnet path)
    return {
      user: null,
      principal: {
        user_id: "sys:legacy",
        roles: ["admin"],
        org_ids: [],
        active_org_id: null,
        via: "sys",
      },
    };
  }
  if (!res.ok) {
    throw new BffApiError(res.status, await readBffErrorDetail(res));
  }
  return res.json() as Promise<AuthSession>;
}

export async function login(email: string, password: string): Promise<AuthSession> {
  return authJson<AuthSession>("/api/bff/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export async function logout(): Promise<void> {
  await authFetch("/api/bff/auth/logout", { method: "POST" });
}

export async function acceptInvite(body: {
  token: string;
  password: string;
  display_name?: string;
}): Promise<AuthSession> {
  return authJson<AuthSession>("/api/bff/auth/accept-invite", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchOrgs(): Promise<{ orgs: OrgRow[]; active_org_id: string | null }> {
  return authJson("/api/bff/orgs");
}

export async function createOrg(name: string): Promise<{ org: OrgRow }> {
  return authJson("/api/bff/orgs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function fetchLinkStatus(): Promise<LinkStatus> {
  return authJson("/api/bff/auth/links");
}

export async function createLinkCode(
  platform?: string | null,
): Promise<{ token: string; instructions: string; platform: string | null }> {
  return authJson("/api/bff/auth/links/code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ platform: platform ?? null }),
  });
}

export async function unlinkPlatform(platform: string): Promise<void> {
  await authJson(`/api/bff/auth/links/${platform}`, { method: "DELETE" });
}
