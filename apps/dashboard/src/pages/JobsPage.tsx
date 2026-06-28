import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { displayAgentName } from "@/lib/agents";
import { fetchAgentStatus, fetchAgents } from "@/lib/api";

export function JobsPage() {
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const { data: status = [] } = useQuery({
    queryKey: ["agent-status-jobs"],
    queryFn: () => fetchAgentStatus(),
    refetchInterval: 15_000,
  });

  return (
    <div className="fd-scroll flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="font-[family-name:var(--font-head)] text-2xl font-bold">Jobs</h1>
          <p className="text-sm text-muted-foreground">
            Workers et file d'exécution — flux live #1772 en cours de câblage.
          </p>
        </div>

        <Card className="border-0 bg-muted/20 shadow-none">
          <CardHeader>
            <CardTitle className="text-base">Workers</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {agents.map((agent) => {
              const health = status.find((s) => s.agent === agent);
              return (
                <div
                  key={agent}
                  className="flex items-center justify-between rounded-lg bg-background/40 px-3 py-2.5"
                >
                  <div>
                    <p className="text-sm font-medium">{displayAgentName(agent)}</p>
                    <p className="text-xs text-muted-foreground">
                      Harness {health?.harness ?? "claude-cli"}
                    </p>
                  </div>
                  <Badge variant={health?.online ? "success" : "secondary"}>
                    {health?.online ? "Actif" : "Inactif"}
                  </Badge>
                </div>
              );
            })}
          </CardContent>
        </Card>

        <Card className="border-0 bg-muted/20 shadow-none">
          <CardHeader>
            <CardTitle className="text-base">File d'attente</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              Abonnement <code className="text-xs">factory.event.*</code> — panneau live jobs
              (#1772). Les événements s'afficheront ici dès que le hub publiera les enveloppes
              WorkEnvelope.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
