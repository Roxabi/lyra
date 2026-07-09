import { parseBffResponse } from "@/shared/api/client";
import { operatorAuthHeaders } from "@/shared/api/operator-auth";

export async function bffFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const auth = operatorAuthHeaders();
  for (const [key, value] of Object.entries(auth)) {
    if (typeof value === "string") headers.set(key, value);
  }
  return fetch(path, { ...init, headers });
}

export async function bffJson<T>(path: string, init?: RequestInit): Promise<T> {
  return parseBffResponse<T>(await bffFetch(path, init));
}
