import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PopoverSelect } from "@/components/ui/popover-select";
import { Textarea } from "@/components/ui/textarea";
import { displayAgentName } from "@/lib/agents";
import { fetchAgentStatus, fetchAgents, fetchJobs, launchJob, steerJob } from "@/lib/api";

function statusVariant(status: string): "success" | "secondary" | "destructive" {
  if (status === "open") return "success";
  if (status === "closing") return "secondary";
  return "destructive";
}

export function JobsPage() {
  const queryClient = useQueryClient();
  const [launchAgent, setLaunchAgent] = useState("");
  const [launchPrompt, setLaunchPrompt] = useState("");
  const [steerTexts, setSteerTexts] = useState<Record<string, string>>({});
  const [launchFeedback, setLaunchFeedback] = useState<string | null>(null);

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

  const selectedAgent = launchAgent || agents[0] || "";

  const launchMutation = useMutation({
    mutationFn: () =>
      launchJob({
        agent: selectedAgent,
        prompt: launchPrompt.trim(),
        job_name: "omp",
      }),
    onSuccess: (res) => {
      setLaunchFeedback(res.accepted ? `Job ${res.job_id} lancé` : res.message);
      setLaunchPrompt("");
      void queryClient.invalidateQueries({ queryKey: ["jobs-live"] });
    },
    onError: () => setLaunchFeedback("Échec du lancement"),
  });

  const steerMutation = useMutation({
    mutationFn: ({ jobId, text }: { jobId: string; text: string }) => steerJob(jobId, text),
    onSuccess: (_res, vars) => {
      setSteerTexts((prev) => ({ ...prev, [vars.jobId]: "" }));
    },
  });

  return (
    <div className="fd-scroll flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="font-[family-name:var(--font-head)] text-2xl font-bold">Jobs</h1>
          <p className="text-sm text-muted-foreground">
            Jobs actifs, lancement manuel et steer via le BFF dashboard (#1773).
          </p>
        </div>

        <Card className="border-0 bg-muted/20 shadow-none">
          <CardHeader>
            <CardTitle className="text-base">Lancer un job OMP</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <PopoverSelect
                label="Agent"
                value={selectedAgent}
                options={agents.map((a) => ({
                  value: a,
                  label: displayAgentName(a),
                }))}
                onChange={setLaunchAgent}
              />
              <Badge variant="secondary">factory.jobs.omp</Badge>
            </div>
            <Textarea
              value={launchPrompt}
              onChange={(e) => setLaunchPrompt(e.target.value)}
              placeholder="Prompt opérateur — publié comme WorkEnvelope sur NATS"
              rows={3}
            />
            <div className="flex items-center gap-3">
              <Button
                type="button"
                disabled={!launchPrompt.trim() || !selectedAgent || launchMutation.isPending}
                onClick={() => launchMutation.mutate()}
              >
                {launchMutation.isPending ? "Envoi…" : "Lancer"}
              </Button>
              {launchFeedback ? (
                <p className="text-xs text-muted-foreground">{launchFeedback}</p>
              ) : null}
            </div>
          </CardContent>
        </Card>

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
                      <th className="pb-2 pr-3 font-medium">Démarré</th>
                      <th className="pb-2 font-medium">Steer</th>
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
                        <td className="py-2.5 pr-3 text-xs text-muted-foreground">
                          {new Date(job.started_at).toLocaleString()}
                        </td>
                        <td className="py-2.5">
                          <div className="flex min-w-[12rem] items-center gap-2">
                            <Input
                              value={steerTexts[job.job_id] ?? ""}
                              onChange={(e) =>
                                setSteerTexts((prev) => ({
                                  ...prev,
                                  [job.job_id]: e.target.value,
                                }))
                              }
                              placeholder="Steer…"
                              className="h-8 text-xs"
                            />
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              className="h-8 shrink-0 px-2 text-xs"
                              disabled={
                                !(steerTexts[job.job_id] ?? "").trim() || steerMutation.isPending
                              }
                              onClick={() =>
                                steerMutation.mutate({
                                  jobId: job.job_id,
                                  text: (steerTexts[job.job_id] ?? "").trim(),
                                })
                              }
                            >
                              →
                            </Button>
                          </div>
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
