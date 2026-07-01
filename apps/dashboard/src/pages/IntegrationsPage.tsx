import { Badge } from "@astryxdesign/core/Badge";
import { Card } from "@astryxdesign/core/Card";
import { Stack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { PageIntro } from "@/components/layout/PageIntro";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type ConnectorInstallation,
  deleteConnectorInstallation,
  fetchConnectorInstallations,
  fetchConnectors,
  fetchGithubInstallUrl,
  upsertConnectorInstallation,
} from "@/lib/api";
import { getOperatorToken, setOperatorToken } from "@/lib/operator-auth";

function InstallationTable({
  installations,
  onDisconnect,
  disconnectingId,
}: {
  installations: ConnectorInstallation[];
  onDisconnect: (externalId: string) => void;
  disconnectingId: string | null;
}) {
  const { t } = useTranslation("integrations");

  if (installations.length === 0) {
    return (
      <p className="rounded-lg bg-background/40 px-4 py-6 text-center text-sm text-muted-foreground">
        {t("table.empty")}
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {installations.map((row) => (
        <div
          key={`${row.connector}:${row.external_id}`}
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-background/40 px-3 py-2"
        >
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground">{t("table.externalId")}</p>
            <p className="truncate font-mono text-sm">{row.external_id}</p>
          </div>
          <div className="flex items-center gap-3">
            <Badge
              variant={row.enabled ? "success" : "neutral"}
              label={row.enabled ? t("table.enabled") : t("table.disabled")}
            />
            {row.enabled ? (
              <Button
                variant="outline"
                size="sm"
                loading={disconnectingId === row.external_id}
                onClick={() => onDisconnect(row.external_id)}
              >
                {t("table.disconnect")}
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
  const { t } = useTranslation("integrations");
  const { t: tc } = useTranslation("common");
  const queryClient = useQueryClient();
  const fieldId = useId();
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
      <Stack gap={4}>
        <Stack gap={1}>
          <Text type="label">{title}</Text>
          <Text type="supporting">{description}</Text>
        </Stack>
        <Stack gap={4}>
          {children}
          <div className="space-y-2">
            <p className="text-sm font-medium">{registerLabel}</p>
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-[12rem] flex-1">
                <label htmlFor={fieldId} className="mb-1 block text-xs text-muted-foreground">
                  {fieldLabel}
                </label>
                <Input
                  id={fieldId}
                  value={externalId}
                  onChange={(e) => setExternalId(e.target.value)}
                  placeholder={fieldLabel}
                />
              </div>
              <Button
                disabled={!externalId.trim()}
                loading={registerMutation.isPending}
                onClick={() => registerMutation.mutate()}
              >
                {registerLabel}
              </Button>
            </div>
          </div>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">{tc("actions.loading")}</p>
          ) : null}
          {isError ? <p className="text-sm text-destructive">{t("errors.load")}</p> : null}
          {data ? (
            <InstallationTable
              installations={data.installations}
              onDisconnect={(id) => disconnectMutation.mutate(id)}
              disconnectingId={disconnectingId}
            />
          ) : null}
        </Stack>
      </Stack>
    </Card>
  );
}

export function IntegrationsPage() {
  const { t } = useTranslation("integrations");
  const { t: tc } = useTranslation("common");
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
    <div className="space-y-6">
      <PageIntro>{t("subtitle")}</PageIntro>

      <Card>
        <Stack gap={4}>
          <Stack gap={1}>
            <Text type="label">{t("auth.title")}</Text>
            <Text type="supporting">{t("auth.hint")}</Text>
          </Stack>
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-[16rem] flex-1">
              <Input
                type="password"
                value={tokenDraft}
                onChange={(e) => setTokenDraft(e.target.value)}
                placeholder={t("auth.placeholder")}
                autoComplete="off"
              />
            </div>
            <Button variant="outline" onClick={() => setOperatorToken(tokenDraft)}>
              {t("auth.save")}
            </Button>
          </div>
        </Stack>
      </Card>

      {catalogLoading ? (
        <p className="text-sm text-muted-foreground">{tc("actions.loading")}</p>
      ) : null}

      {authError ? (
        <p className="text-sm text-destructive">{t("errors.auth")}</p>
      ) : catalogError && !authError ? (
        <p className="text-sm text-destructive">{t("errors.load")}</p>
      ) : null}

      {catalog ? (
        <p className="text-sm text-muted-foreground">
          {t("tenantLabel")}:{" "}
          <span className="font-mono text-foreground">{catalog.factory_tenant}</span>
        </p>
      ) : null}

      <ConnectorSection
        connector="github"
        title={t("github.title")}
        description={t("github.description")}
        registerLabel={t("github.register")}
        fieldLabel={t("github.installationId")}
      >
        <div className="space-y-2">
          {githubInstall ? (
            <Button variant="brand" asChild>
              <a href={githubInstall.url} target="_blank" rel="noopener noreferrer">
                {t("github.install")}
              </a>
            </Button>
          ) : githubInstallError ? (
            <p className="text-sm text-muted-foreground">{t("github.installUnavailable")}</p>
          ) : null}
          <p className="text-xs text-muted-foreground">{t("github.manualTitle")}</p>
        </div>
      </ConnectorSection>

      <ConnectorSection
        connector="cloudflare"
        title={t("cloudflare.title")}
        description={t("cloudflare.description")}
        registerLabel={t("cloudflare.register")}
        fieldLabel={t("cloudflare.accountId")}
      />
    </div>
  );
}
