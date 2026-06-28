import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { displayAgentName } from "@/lib/agents";
import { fetchAgentStatus, fetchAgents, fetchJobs } from "@/lib/api";

function statusVariant(status: string): "success" | "secondary" | "destructive" {
  if (status === "open") return "success";
  if (status === "closing") return "secondary";
  return "destructive";
}

export function JobsPage() {
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const { data: status = [] } = useQuery({
    queryKey: ["agent-status-jobs"],
    queryFn: () => fetchAgentStatus(),
    refetchInterval: 15_000,
  });
  const {
    data: jobs = [],
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["jobs-live"],
    queryFn: fetchJobs,
    refetchInterval: 5_000,
  });

  return (
    <div className="fd-scroll flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="font-[family-name:var(--font-head)] text-2xl font-bold">Jobs</h1>
          <p className="text-sm text-muted-foreground">
            Jobs actifs depuis le registry <code className="text-xs">factory-active-jobs</code> —
            rafraîchi toutes les 5 s.
          </p>
        </div>

        <Card className="border-0 bg-muted/20 shadow-none">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">Jobs en cours</CardTitle>
            <Badge variant="secondary">
              {jobs.length} actif{jobs.length !== 1 ? "s" : ""}
            </Badge>
          </CardHeader>
          <CardContent>
            {isLoading ? <p className="text-sm text-muted-foreground">Chargement…</p> : null}
            {isError ? (
              <p className="text-sm text-destructive">Impossible de charger les jobs.</p>
            ) : null}
            {!isLoading && !isError && jobs.length === 0 ? (
              <p className="rounded-lg bg-background/40 px-4 py-6 text-center text-sm text-muted-foreground">
                Aucun job actif dans le registry.
              </p>
            ) : null}
            {jobs.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="text-xs text-muted-foreground">
                      <th className="pb-2 pr-3 font-medium">Job</th>
                      <th className="pb-2 pr-3 font-medium">Agent</th>
                      <th className="pb-2 pr-3 font-medium">Plateforme</th>
                      <th className="pb-2 pr-3 font-medium">Statut</th>
                      <th className="pb-2 pr-3 font-medium">Mode</th>
                      <th className="pb-2 font-medium">Démarré</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((job) => (
                      <tr key={job.job_id} className="border-t border-border/30">
                        <td className="py-2.5 pr-3">
                          <p className="font-mono text-xs">{job.job_id}</p>
                          <p className="truncate text-[10px] text-muted-foreground">
                            {job.pool_id}
                          </p>
                        </td>
                        <td className="py-2.5 pr-3">
                          {job.agent ? displayAgentName(job.agent) : "—"}
                        </td>
                        <td className="py-2.5 pr-3 capitalize">{job.platform ?? "—"}</td>
                        <td className="py-2.5 pr-3">
                          <Badge variant={statusVariant(job.status)}>{job.status}</Badge>
                        </td>
                        <td className="py-2.5 pr-3 text-xs text-muted-foreground">
                          {job.concurrency_mode}
                        </td>
                        <td className="py-2.5 text-xs text-muted-foreground">
                          {new Date(job.started_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </CardContent>
        </Card>

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
      </div>
    </div>
  );
}
