import { Badge } from "@astryxdesign/core/Badge";
import { Card } from "@astryxdesign/core/Card";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import { Stack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { Robot } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentIdentity } from "@/components/agents/AgentIdentity";
import { PageIntro } from "@/components/layout/PageIntro";
import { EmptyState } from "@/components/ui/empty-state";
import { ListToolbar, ListToolbarHeader, ListToolbarSearch } from "@/components/ui/list-toolbar";
import { PopoverSelect } from "@/components/ui/popover-select";
import { displayAgentName } from "@/lib/agents";
import { fetchAgentStatus, fetchOpsHealth, fetchOpsLogs, type OpsLogPreset } from "@/lib/api";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const LOG_PRESET_OPTIONS: { value: OpsLogPreset; label: string }[] = [
  { value: "hub-errors", label: "Erreurs hub (1h)" },
  { value: "operator-events", label: "Événements converge" },
  { value: "deploy-failures", label: "Échecs deploy (24h)" },
];

const opsRouteApi = getRouteApi("/ops");

function EngineCardsSkeleton() {
  const { t } = useTranslation("common");
  return (
    <div
      role="status"
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
      aria-busy="true"
      aria-label={t("actions.loading")}
    >
      {[0, 1, 2].map((i) => (
        <Card key={i}>
          <Stack gap={3}>
            <Skeleton width={96} height={16} />
            <Skeleton width={64} height={24} radius="rounded" />
          </Stack>
        </Card>
      ))}
    </div>
  );
}

function LogsSkeleton() {
  const { t } = useTranslation("common");
  return (
    <div role="status" className="space-y-2" aria-busy="true" aria-label={t("actions.loading")}>
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} width="100%" height={40} />
      ))}
    </div>
  );
}

export function OpsPage() {
  const { t } = useTranslation("ops");
  const { t: tc } = useTranslation("common");
  const { container } = opsRouteApi.useSearch();
  const [logPreset, setLogPreset] = useState<OpsLogPreset>("hub-errors");
  const [agentSearch, setAgentSearch] = useState("");
  const debouncedAgentSearch = useDebouncedValue(agentSearch, 300);

  useEffect(() => {
    if (container) {
      setLogPreset("container-journal");
    }
  }, [container]);

  const { data: status = [], isLoading: statusLoading } = useQuery({
    queryKey: ["agent-status-ops"],
    queryFn: () => fetchAgentStatus(),
    refetchInterval: 15_000,
  });

  const probeAgent = status.find((s) => s.in_roster)?.agent ?? status[0]?.agent;
  const { data: ompHarnessStatus = [] } = useQuery({
    queryKey: ["agent-status-ops-omp", probeAgent],
    queryFn: () => fetchAgentStatus(probeAgent ?? "", "omp-rpc"),
    enabled: Boolean(probeAgent),
    refetchInterval: 15_000,
  });

  const {
    data: engines = [],
    isLoading: enginesLoading,
    isError: enginesError,
  } = useQuery({
    queryKey: ["ops-health"],
    queryFn: fetchOpsHealth,
    refetchInterval: 30_000,
  });

  const {
    data: logs,
    isLoading: logsLoading,
    isError: logsError,
  } = useQuery({
    queryKey: ["ops-logs", logPreset, container],
    queryFn: () => fetchOpsLogs(logPreset, 50, container),
    refetchInterval: 30_000,
  });

  const filteredAgents = useMemo(() => {
    const q = debouncedAgentSearch.trim().toLowerCase();
    if (!q) return status;
    return status.filter((s) => {
      const haystack = [s.agent, displayAgentName(s.agent), s.harness].join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }, [status, debouncedAgentSearch]);

  const clipoolUp = status.some((s) => s.harness === "claude-cli" && s.harness_reachable);
  const ompUp = ompHarnessStatus.some((s) => s.harness === "omp-rpc" && s.harness_reachable);
  const hasAgentFilters = agentSearch.trim().length > 0;

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>{t("subtitle")}</PageIntro>

      {enginesLoading ? <EngineCardsSkeleton /> : null}

      {!enginesLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {engines.map((engine) => (
            <Card key={engine.engine}>
              <Stack gap={4} align="start">
                <Text type="label">{engine.label}</Text>
                <Badge
                  variant={engine.reachable ? "success" : "error"}
                  label={engine.reachable ? tc("status.online") : tc("status.offline")}
                />
                {engine.detail ? (
                  <p className="mt-2 truncate text-xs text-muted-foreground">{engine.detail}</p>
                ) : null}
              </Stack>
            </Card>
          ))}
          {enginesError ? (
            <Card className="sm:col-span-2 lg:col-span-3">
              <Stack gap={4}>
                <p className="text-sm text-destructive" role="alert">
                  {t("engines.loadError")}
                </p>
              </Stack>
            </Card>
          ) : null}
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <Card data-testid="ops-harness-card">
          <Stack gap={4} align="start">
            <Text type="label">{t("harness.clipool")}</Text>
            <Badge
              variant={clipoolUp ? "success" : "error"}
              label={clipoolUp ? tc("status.online") : tc("status.offline")}
            />
          </Stack>
        </Card>
        <Card data-testid="ops-harness-card">
          <Stack gap={4} align="start">
            <Text type="label">{t("harness.omp")}</Text>
            <Badge
              variant={ompUp ? "success" : "error"}
              label={ompUp ? tc("status.online") : tc("status.offline")}
            />
          </Stack>
        </Card>
      </div>

      <Card>
        <Stack gap={4}>
          <Stack
            direction="horizontal"
            justify="between"
            align="center"
            gap={4}
            className="flex-wrap"
          >
            <Stack gap={1}>
              <Text type="label">{t("logs.title")}</Text>
              {container ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("logs.containerFilter", { container })}
                </p>
              ) : null}
              {logs?.query ? (
                <p className="mt-1 font-mono text-[10px] text-muted-foreground">{logs.query}</p>
              ) : null}
            </Stack>
            {container ? null : (
              <PopoverSelect
                label={t("logs.presetLabel")}
                value={logPreset}
                options={LOG_PRESET_OPTIONS}
                onChange={(v) => setLogPreset(v as OpsLogPreset)}
              />
            )}
          </Stack>
          {logsLoading ? <LogsSkeleton /> : null}
          {logsError ? (
            <p className="text-sm text-destructive" role="alert">
              {t("logs.loadError")}
            </p>
          ) : null}
          {logs && !logs.engine_reachable ? (
            <p className="text-sm text-muted-foreground">{t("logs.unreachable")}</p>
          ) : null}
          {logs?.engine_reachable && logs.entries.length === 0 ? (
            <EmptyState title={t("logs.empty")} hint={t("logs.emptyHint")} className="py-8" />
          ) : null}
          {logs && logs.entries.length > 0 ? (
            <div className="max-h-80 space-y-1 overflow-y-auto rounded-lg bg-background/40 p-3 font-mono text-xs">
              {logs.entries.map((entry) => (
                <div
                  key={`${entry.timestamp}:${entry.line}`}
                  className="border-b border-border/20 py-1.5"
                >
                  <span className="text-muted-foreground">
                    {new Date(entry.timestamp).toLocaleString()}
                  </span>
                  <pre className="mt-0.5 whitespace-pre-wrap break-words text-foreground">
                    {entry.line}
                  </pre>
                </div>
              ))}
            </div>
          ) : null}
        </Stack>
      </Card>

      <div className="space-y-4">
        <ListToolbar>
          <ListToolbarHeader
            meta={
              statusLoading
                ? tc("actions.loading")
                : t("agents.count", { count: filteredAgents.length })
            }
          />
          <ListToolbarSearch
            value={agentSearch}
            onChange={setAgentSearch}
            placeholder={t("agents.searchPlaceholder")}
            aria-label={tc("search")}
          />
        </ListToolbar>

        {statusLoading ? (
          <div
            role="status"
            className="space-y-2"
            aria-busy="true"
            aria-label={tc("actions.loading")}
          >
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} width="100%" height={56} radius={4} />
            ))}
          </div>
        ) : null}

        {!statusLoading && filteredAgents.length === 0 ? (
          <EmptyState
            icon={hasAgentFilters ? undefined : Robot}
            title={hasAgentFilters ? t("agents.emptyFiltered") : t("agents.empty")}
            hint={hasAgentFilters ? undefined : t("agents.emptyHint")}
          />
        ) : null}

        {!statusLoading && filteredAgents.length > 0 ? (
          <div className="space-y-2">
            {filteredAgents.map((s) => (
              <div
                key={s.agent}
                className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-card px-3 py-2.5 shadow-sm transition-colors hover:bg-muted/15"
              >
                <AgentIdentity agentId={s.agent} avatarSize="sm" />
                <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
                  <Badge
                    variant={s.in_roster ? "success" : "neutral"}
                    label={
                      <>
                        {t("agents.roster")} {s.in_roster ? "✓" : "✗"}
                      </>
                    }
                  />
                  <Badge
                    variant={s.harness_reachable ? "success" : "error"}
                    label={
                      <>
                        {t("agents.harness")} {s.harness_reachable ? "✓" : "✗"}
                      </>
                    }
                  />
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
