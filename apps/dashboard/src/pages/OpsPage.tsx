import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PageIntro } from "@/components/layout/PageIntro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PopoverSelect } from "@/components/ui/popover-select";
import { displayAgentName } from "@/lib/agents";
import { fetchAgentStatus, fetchOpsHealth, fetchOpsLogs, type OpsLogPreset } from "@/lib/api";

const LOG_PRESET_OPTIONS: { value: OpsLogPreset; label: string }[] = [
  { value: "hub-errors", label: "Erreurs hub (1h)" },
  { value: "operator-events", label: "Événements converge" },
  { value: "deploy-failures", label: "Échecs deploy (24h)" },
];

export function OpsPage() {
  const { t } = useTranslation("ops");
  const { t: tc } = useTranslation("common");
  const [logPreset, setLogPreset] = useState<OpsLogPreset>("hub-errors");

  const { data: status = [] } = useQuery({
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

  const { data: engines = [], isError: enginesError } = useQuery({
    queryKey: ["ops-health"],
    queryFn: fetchOpsHealth,
    refetchInterval: 30_000,
  });

  const {
    data: logs,
    isLoading: logsLoading,
    isError: logsError,
  } = useQuery({
    queryKey: ["ops-logs", logPreset],
    queryFn: () => fetchOpsLogs(logPreset),
    refetchInterval: 30_000,
  });

  const clipoolUp = status.some((s) => s.harness === "claude-cli" && s.harness_reachable);
  // Default status uses harness=claude-cli — probe omp-rpc explicitly for the worker lane.
  const ompUp = ompHarnessStatus.some((s) => s.harness === "omp-rpc" && s.harness_reachable);

  return (
    <div className="space-y-6">
      <PageIntro>{t("subtitle")}</PageIntro>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {engines.map((engine) => (
          <Card key={engine.engine} className="dashboard-surface border-border/60 shadow-none">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">{engine.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <Badge variant={engine.reachable ? "success" : "destructive"}>
                {engine.reachable ? tc("status.online") : tc("status.offline")}
              </Badge>
              {engine.detail ? (
                <p className="mt-2 truncate text-xs text-muted-foreground">{engine.detail}</p>
              ) : null}
            </CardContent>
          </Card>
        ))}
        {enginesError ? (
          <Card className="dashboard-surface border-border/60 shadow-none sm:col-span-2 lg:col-span-3">
            <CardContent className="pt-6">
              <p className="text-sm text-destructive">{t("engines.loadError")}</p>
            </CardContent>
          </Card>
        ) : null}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="dashboard-surface border-border/60 shadow-none">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-muted-foreground">{t("harness.clipool")}</CardTitle>
          </CardHeader>
          <CardContent>
            <Badge variant={clipoolUp ? "success" : "destructive"}>
              {clipoolUp ? tc("status.online") : tc("status.offline")}
            </Badge>
          </CardContent>
        </Card>
        <Card className="dashboard-surface border-border/60 shadow-none">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-muted-foreground">{t("harness.omp")}</CardTitle>
          </CardHeader>
          <CardContent>
            <Badge variant={ompUp ? "success" : "destructive"}>
              {ompUp ? tc("status.online") : tc("status.offline")}
            </Badge>
          </CardContent>
        </Card>
      </div>

      <Card className="dashboard-surface border-border/60 shadow-none">
        <CardHeader className="flex flex-row items-center justify-between gap-4">
          <div>
            <CardTitle className="text-base">{t("logs.title")}</CardTitle>
            {logs?.query ? (
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">{logs.query}</p>
            ) : null}
          </div>
          <PopoverSelect
            label={t("logs.presetLabel")}
            value={logPreset}
            options={LOG_PRESET_OPTIONS}
            onChange={(v) => setLogPreset(v as OpsLogPreset)}
          />
        </CardHeader>
        <CardContent>
          {logsLoading ? (
            <p className="text-sm text-muted-foreground">{tc("actions.loading")}</p>
          ) : null}
          {logsError ? <p className="text-sm text-destructive">{t("logs.loadError")}</p> : null}
          {logs && !logs.engine_reachable ? (
            <p className="text-sm text-muted-foreground">{t("logs.unreachable")}</p>
          ) : null}
          {logs?.engine_reachable && logs.entries.length === 0 ? (
            <p className="rounded-lg bg-background/40 px-4 py-6 text-center text-sm text-muted-foreground">
              {t("logs.empty")}
            </p>
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
        </CardContent>
      </Card>

      <Card className="dashboard-surface border-border/60 shadow-none">
        <CardHeader>
          <CardTitle className="text-base">{t("agents.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {status.map((s) => (
            <div
              key={s.agent}
              className="flex items-center justify-between rounded-lg bg-background/40 px-3 py-2"
            >
              <span className="text-sm">{displayAgentName(s.agent)}</span>
              <span className="text-xs text-muted-foreground">
                roster {s.in_roster ? "✓" : "✗"} · harness {s.harness_reachable ? "✓" : "✗"}
              </span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
