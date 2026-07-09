import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import {
  deleteConnectorInstallation,
  fetchConnectorInstallations,
  fetchConnectors,
  fetchGithubInstallUrl,
  upsertConnectorInstallation,
} from "@/features/integrations/api";
import type { ConnectorInstallation } from "@/shared/api/bff-types";
import { getOperatorToken, setOperatorToken } from "@/shared/api/operator-auth";
import { PageIntro } from "@/shared/components/page-intro";

function InstallationTable({
  installations,
  onDisconnect,
  disconnectingId,
}: {
  installations: ConnectorInstallation[];
  onDisconnect: (externalId: string) => void;
  disconnectingId: string | null;
}) {
  if (installations.length === 0) {
    return (
      <p className="rounded-lg bg-muted/30 px-4 py-6 text-center text-sm text-muted-foreground">
        No installations registered.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {installations.map((row) => (
        <div
          key={`${row.connector}:${row.external_id}`}
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-muted/30 px-3 py-2"
        >
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground">External ID</p>
            <p className="truncate font-mono text-sm">{row.external_id}</p>
          </div>
          <div className="flex items-center gap-3">
            <Badge variant={row.enabled ? "default" : "outline"}>
              {row.enabled ? "Enabled" : "Disabled"}
            </Badge>
            {row.enabled ? (
              <Button
                variant="outline"
                size="sm"
                disabled={disconnectingId === row.external_id}
                onClick={() => onDisconnect(row.external_id)}
              >
                {disconnectingId === row.external_id ? <Spinner className="mr-1" /> : null}
                Disconnect
              </Button>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function ConnectorSection({
  connector,
  title,
  description,
  registerLabel,
  fieldLabel,
  children,
}: {
  connector: string;
  title: string;
  description: string;
  registerLabel: string;
  fieldLabel: string;
  children?: ReactNode;
}) {
  const queryClient = useQueryClient();
  const [externalId, setExternalId] = useState("");
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["connector-installations", connector],
    queryFn: () => fetchConnectorInstallations(connector),
    refetchInterval: 15_000,
  });

  const registerMutation = useMutation({
    mutationFn: () => upsertConnectorInstallation(connector, { external_id: externalId.trim() }),
    onSuccess: () => {
      setExternalId("");
      void queryClient.invalidateQueries({ queryKey: ["connector-installations", connector] });
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => deleteConnectorInstallation(connector, id),
    onMutate: (id) => setDisconnectingId(id),
    onSettled: () => setDisconnectingId(null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["connector-installations", connector] });
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {children}
        <div className="space-y-2">
          <p className="text-sm font-medium">{registerLabel}</p>
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-48 flex-1 space-y-2">
              <Label htmlFor={`${connector}-external-id`}>{fieldLabel}</Label>
              <Input
                id={`${connector}-external-id`}
                value={externalId}
                onChange={(e) => setExternalId(e.target.value)}
                placeholder={fieldLabel}
              />
            </div>
            <Button
              disabled={!externalId.trim() || registerMutation.isPending}
              onClick={() => registerMutation.mutate()}
            >
              {registerMutation.isPending ? <Spinner className="mr-1.5" /> : null}
              {registerLabel}
            </Button>
          </div>
        </div>
        {isLoading ? <p className="text-sm text-muted-foreground">Loading…</p> : null}
        {isError ? (
          <p className="text-sm text-destructive" role="alert">
            Failed to load installations.
          </p>
        ) : null}
        {data ? (
          <InstallationTable
            installations={data.installations}
            onDisconnect={(id) => disconnectMutation.mutate(id)}
            disconnectingId={disconnectingId}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

export function IntegrationsPage() {
  const [tokenDraft, setTokenDraft] = useState(getOperatorToken() ?? "");

  const {
    data: catalog,
    isLoading: catalogLoading,
    isError: catalogError,
    error: catalogErr,
  } = useQuery({
    queryKey: ["connectors-catalog"],
    queryFn: fetchConnectors,
  });

  const { data: githubInstall, isError: githubInstallError } = useQuery({
    queryKey: ["github-install-url"],
    queryFn: fetchGithubInstallUrl,
    retry: false,
  });

  const authError =
    catalogError &&
    catalogErr instanceof Error &&
    (catalogErr.message === "auth" || catalogErr.message.includes("401"));

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>Connect GitHub and Cloudflare to your factory tenant.</PageIntro>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Operator token</CardTitle>
          <CardDescription>
            Bearer token for BFF admin and connector endpoints. Stored in session storage.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-64 flex-1 space-y-2">
              <Label htmlFor="operator-token">Token</Label>
              <Input
                id="operator-token"
                type="password"
                value={tokenDraft}
                onChange={(e) => setTokenDraft(e.target.value)}
                placeholder="Bearer token"
              />
            </div>
            <Button variant="outline" onClick={() => setOperatorToken(tokenDraft)}>
              Save token
            </Button>
          </div>
        </CardContent>
      </Card>

      {catalogLoading ? <p className="text-sm text-muted-foreground">Loading connectors…</p> : null}

      {authError ? (
        <p className="text-sm text-destructive" role="alert">
          Authentication required. Set a valid operator token above.
        </p>
      ) : catalogError && !authError ? (
        <p className="text-sm text-destructive" role="alert">
          Failed to load connector catalog.
        </p>
      ) : null}

      {catalog ? (
        <p className="text-sm text-muted-foreground">
          Tenant: <span className="font-mono text-foreground">{catalog.factory_tenant}</span>
        </p>
      ) : null}

      <ConnectorSection
        connector="github"
        title="GitHub"
        description="Install the GitHub App or register an installation ID manually."
        registerLabel="Register installation"
        fieldLabel="Installation ID"
      >
        <div className="space-y-2">
          {githubInstall ? (
            <Button
              variant="default"
              render={<a href={githubInstall.url} target="_blank" rel="noopener noreferrer" />}
            >
              Install GitHub App
            </Button>
          ) : githubInstallError ? (
            <p className="text-sm text-muted-foreground">GitHub App install URL unavailable.</p>
          ) : null}
          <p className="text-xs text-muted-foreground">
            Or register an installation ID manually below.
          </p>
        </div>
      </ConnectorSection>

      <ConnectorSection
        connector="cloudflare"
        title="Cloudflare"
        description="Register your Cloudflare account ID for deploy integrations."
        registerLabel="Register account"
        fieldLabel="Account ID"
      />
    </div>
  );
}
