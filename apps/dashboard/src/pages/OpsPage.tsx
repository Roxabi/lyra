import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { displayAgentName } from "@/lib/agents";
import { fetchAgentStatus } from "@/lib/api";

export function OpsPage() {
  const { data: status = [] } = useQuery({
    queryKey: ["agent-status-ops"],
    queryFn: () => fetchAgentStatus(),
    refetchInterval: 15_000,
  });

  const clipoolUp = status.some((s) => s.harness === "claude-cli" && s.harness_reachable);
  const ompUp = status.some((s) => s.harness === "omp-rpc" && s.harness_reachable);

  return (
    <div className="fd-scroll flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="font-[family-name:var(--font-head)] text-2xl font-bold">Supervision</h1>
          <p className="text-sm text-muted-foreground">
            Observabilité — Loki, traces et coûts (#1774) via le BFF dashboard.
          </p>
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

        <Card className="border-0 bg-muted/20 shadow-none">
          <CardHeader>
            <CardTitle className="text-base">Logs & métriques</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              Requêtes Loki/Prom/Langfuse proxifiées par le BFF — intégration prévue issue #1774.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
