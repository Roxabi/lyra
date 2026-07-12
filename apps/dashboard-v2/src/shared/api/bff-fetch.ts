import { getActiveOrgId } from "@/features/auth/api";
import { parseBffResponse } from "@/shared/api/client";
import { operatorAuthHeaders } from "@/shared/api/operator-auth";

/** Unified BFF fetch: session cookie + optional API key + active org header. */
export async function bffFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const auth = operatorAuthHeaders();
  for (const [key, value] of Object.entries(auth)) {
    if (typeof value === "string") headers.set(key, value);
  }
  const org = getActiveOrgId();
  if (org) headers.set("X-Factory-Org-Id", org);
  return fetch(path, { ...init, headers, credentials: "include" });
}

export async function bffJson<T>(path: string, init?: RequestInit): Promise<T> {
  return parseBffResponse<T>(await bffFetch(path, init));
}
