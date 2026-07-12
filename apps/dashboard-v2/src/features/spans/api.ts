import { bffFetch } from "@/shared/api/bff-fetch";
import type { SpanRow } from "@/shared/api/bff-types";

export async function fetchSpans(params?: {
  pool_id?: string;
  job_id?: string;
  component?: string;
  page?: number;
  page_size?: number;
}): Promise<{
  items: SpanRow[];
  total: number;
  page: number;
  page_size: number;
}> {
  const qs = new URLSearchParams();
  if (params?.pool_id) qs.set("pool_id", params.pool_id);
  if (params?.job_id) qs.set("job_id", params.job_id);
  if (params?.component) qs.set("component", params.component);
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  const suffix = qs.toString();
  const res = await bffFetch(`/api/bff/spans${suffix ? `?${suffix}` : ""}`);
  if (!res.ok) throw new Error("spans fetch failed");
  return res.json() as Promise<{
    items: SpanRow[];
    total: number;
    page: number;
    page_size: number;
  }>;
}

export type { SpanRow };
