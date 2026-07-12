import { bffFetch } from "@/shared/api/bff-fetch";
import type { ConnectorDescriptor, ConnectorInstallation } from "@/shared/api/bff-types";

async function connectorFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await bffFetch(path, init);
  if (res.status === 401) throw new Error("auth");
  return res;
}

export async function fetchConnectors(): Promise<{
  connectors: ConnectorDescriptor[];
  factory_tenant: string;
}> {
  const res = await connectorFetch("/api/bff/connectors");
  if (!res.ok) throw new Error("connectors fetch failed");
  return res.json() as Promise<{ connectors: ConnectorDescriptor[]; factory_tenant: string }>;
}

export async function fetchGithubInstallUrl(): Promise<{ url: string; app_slug: string }> {
  const res = await connectorFetch("/api/bff/connectors/github/install-url");
  if (!res.ok) throw new Error("github install url fetch failed");
  return res.json() as Promise<{ url: string; app_slug: string }>;
}

export async function fetchConnectorInstallations(
  connector: string,
): Promise<{ installations: ConnectorInstallation[] }> {
  const res = await connectorFetch(
    `/api/bff/connectors/${encodeURIComponent(connector)}/installations`,
  );
  if (!res.ok) throw new Error("installations fetch failed");
  return res.json() as Promise<{ installations: ConnectorInstallation[] }>;
}

export async function upsertConnectorInstallation(
  connector: string,
  body: { external_id: string; factory_tenant?: string },
): Promise<{ ok: boolean }> {
  const res = await connectorFetch(
    `/api/bff/connectors/${encodeURIComponent(connector)}/installations`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error("installation upsert failed");
  return res.json() as Promise<{ ok: boolean }>;
}

export async function deleteConnectorInstallation(
  connector: string,
  externalId: string,
): Promise<{ ok: boolean }> {
  const res = await connectorFetch(
    `/api/bff/connectors/${encodeURIComponent(connector)}/installations/${encodeURIComponent(externalId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error("installation delete failed");
  return res.json() as Promise<{ ok: boolean }>;
}
