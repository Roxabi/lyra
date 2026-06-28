import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PopoverSelect } from "@/components/ui/popover-select";
import { displayAgentName } from "@/lib/agents";
import {
  fetchAgentStatus,
  fetchOpsHealth,
  fetchOpsLogs,
  type OpsLogPreset,
} from "@/lib/api";

const LOG_PRESET_OPTIONS: { value: OpsLogPreset; label: string }[] = [
  { value: "hub-errors", label: "Erreurs hub (1h)" },
  { value: "operator-events", label: "Événements converge" },
  { value: "deploy-failures", label: "Échecs deploy (24h)" },
];

export function OpsPage() {
  const [logPreset, setLogPreset] = useState<OpsLogPreset>("hub-errors");

  const { data: status = [] } = useQuery({
    queryKey: ["agent-status-ops"],
    queryFn: () => fetchAgentStatus(),
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
  const ompUp = status.some((s) => s.harness === "omp-rpc" && s.harness_reachable);

  return (
    <div className="fd-scroll flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="font-[family-name:var(--font-head)] text-2xl font-bold">Supervision</h1>
          <p className="text-sm text-muted-foreground">
            Observabilité proxifiée par le BFF — Loki, Langfuse et OTel (#1774).
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {engines.map((engine) => (
            <Card key={engine.engine} className="border-0 bg-muted/20 shadow-none">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-muted-foreground">{engine.label}</CardTitle>
              </CardHeader>
              <CardContent>
                <Badge variant={engine.reachable ? "success" : "destructive"}>
                  {engine.reachable ? "En ligne" : "Hors ligne"}
                </Badge>
                {engine.detail ? (
                  <p className="mt-2 truncate text-xs text-muted-foreground">{engine.detail}</p>
                ) : null}
              </CardContent>
            </Card>
          ))}
          {enginesError ? (
            <Card className="border-0 bg-muted/20 shadow-none sm:col-span-2 lg:col-span-3">
              <CardContent className="pt-6">
                <p className="text-sm text-destructive">
                  Impossible de charger l&apos;état des moteurs d&apos;observabilité.
                </p>
              </CardContent>
            </Card>
          ) : null}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Card className="border-0 bg-muted/20 shadow-none">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">Clipool worker</CardTitle>
            </CardHeader>
            <CardContent>
              <Badge variant={clipoolUp ? "success" : "destructive"}>
                {clipoolUp ? "Heartbeat OK" : "Hors ligne"}
              </Badge>
            </CardContent>
          </Card>
          <Card className="border-0 bg-muted/20 shadow-none">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">OMP worker</CardTitle>
            </CardHeader>
            <CardContent>
              <Badge variant={ompUp ? "success" : "destructive"}>
                {ompUp ? "Heartbeat OK" : "Hors ligne"}
              </Badge>
            </CardContent>
          </Card>
        </div>

        <Card className="border-0 bg-muted/20 shadow-none">
          <CardHeader className="flex flex-row items-center justify-between gap-4">
            <div>
              <CardTitle className="text-base">Logs Loki</CardTitle>
              {logs?.query ? (
                <p className="mt-1 font-mono text-[10px] text-muted-foreground">{logs.query}</p>
              ) : null}
            </div>
            <PopoverSelect
              label="Preset Loki"
              value={logPreset}
              options={LOG_PRESET_OPTIONS}
              onChange={(v) => setLogPreset(v as OpsLogPreset)}
            />
          </CardHeader>
          <CardContent>
            {logsLoading ? (
              <p className="text-sm text-muted-foreground">Chargement des logs…</p>
            ) : null}
            {logsError ? (
              <p className="text-sm text-destructive">Impossible de charger les logs.</p>
            ) : null}
            {logs && !logs.engine_reachable ? (
              <p className="text-sm text-muted-foreground">
                Loki injoignable — vérifiez <code className="text-xs">factory-loki</code> sur M₁.
              </p>
            ) : null}
            {logs && logs.engine_reachable && logs.entries.length === 0 ? (
              <p className="rounded-lg bg-background/40 px-4 py-6 text-center text-sm text-muted-foreground">
                Aucune entrée pour ce preset sur la fenêtre récente.
              </p>
            ) : null}
            {logs && logs.entries.length > 0 ? (
              <div className="max-h-80 space-y-1 overflow-y-auto rounded-lg bg-background/40 p-3 font-mono text-xs">
                {logs.entries.map((entry, idx) => (
                  <div key={`${entry.timestamp}-${idx}`} className="border-b border-border/20 py-1.5">
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

        <Card className="border-0 bg-muted/20 shadow-none">
          <CardHeader>
            <CardTitle className="text-base">Agents monitorés</CardTitle>
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
    </div>
  );
}