import { Warning } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { PageIntro } from "@/components/layout/PageIntro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { displayAgentName } from "@/lib/agents";
import { fetchAgentStatus, fetchAgents, fetchJobs, fetchOpsHealth } from "@/lib/api";
import { loadTabs } from "@/lib/chats-storage";

function statusVariant(status: string): "success" | "secondary" | "destructive" {
  if (status === "open") return "success";
  if (status === "closing") return "secondary";
  return "destructive";
}

export function DashboardHome() {
  const { t } = useTranslation("dashboard");
  const { t: tc } = useTranslation("common");
  const tabs = loadTabs();

  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const { data: status = [], isLoading: statusLoading } = useQuery({
    queryKey: ["agent-status-all"],
    queryFn: () => fetchAgentStatus(),
    refetchInterval: 30_000,
  });
  const { data: jobs = [], isLoading: jobsLoading } = useQuery({
    queryKey: ["jobs-overview"],
    queryFn: fetchJobs,
    refetchInterval: 15_000,
  });
  const { data: engines = [] } = useQuery({
    queryKey: ["ops-health-overview"],
    queryFn: fetchOpsHealth,
    refetchInterval: 30_000,
  });

  const offlineAgents = status.filter((s) => !s.online).length;
  const enginesDown = engines.filter((e) => !e.reachable).length;
  const alertParts: string[] = [];

  if (offlineAgents > 0) {
    alertParts.push(t("alerts.agentsOffline", { count: offlineAgents }));
  }
  if (jobs.length > 0) {
    alertParts.push(t("alerts.jobsActive", { count: jobs.length }));
  }
  if (enginesDown > 0) {
    alertParts.push(t("alerts.enginesDown", { count: enginesDown }));
  }

  const previewJobs = jobs.slice(0, 3);
  const previewTabs = tabs.slice(0, 3);

  return (
    <div className="space-y-6">
      <PageIntro>{t("subtitle")}</PageIntro>

      <section
        className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 bg-card px-4 py-3"
        aria-live="polite"
      >
        <Warning className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <p className="text-sm">
          {alertParts.length > 0 ? (
            <span className="font-medium text-foreground">{alertParts.join(" · ")}</span>
          ) : (
            <span className="text-muted-foreground">{t("alerts.allClear")}</span>
          )}
        </p>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="dashboard-surface border-border/60 shadow-none">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
            <CardTitle className="text-sm font-semibold">{t("agents.title")}</CardTitle>
            <Badge variant="secondary" className="tabular-nums">
              {status.length}
            </Badge>
          </CardHeader>
          <CardContent className="p-0">
            {statusLoading ? (
              <p className="px-6 pb-6 text-sm text-muted-foreground">{tc("actions.loading")}</p>
            ) : status.length === 0 ? (
              <p className="px-6 pb-6 text-sm text-muted-foreground">{t("agents.empty")}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-t border-border/40 text-xs text-muted-foreground">
                      <th className="px-6 py-2 font-medium">{t("agents.colAgent")}</th>
                      <th className="px-3 py-2 font-medium">{t("agents.colHarness")}</th>
                      <th className="px-6 py-2 font-medium">{t("agents.colStatus")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.map((s) => (
                      <tr key={s.agent} className="border-t border-border/30">
                        <td className="px-6 py-2.5 font-medium">{displayAgentName(s.agent)}</td>
                        <td className="px-3 py-2.5 text-xs text-muted-foreground">{s.harness}</td>
                        <td className="px-6 py-2.5">
                          <Badge variant={s.online ? "success" : "destructive"}>
                            {s.online ? tc("status.online") : tc("status.offline")}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="dashboard-surface border-border/60 shadow-none">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
            <CardTitle className="text-sm font-semibold">{t("jobs.title")}</CardTitle>
            <Button variant="ghost" size="sm" className="h-8 text-xs" asChild>
              <Link to="/jobs">{tc("actions.viewAll")}</Link>
            </Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {jobsLoading ? (
              <p className="text-sm text-muted-foreground">{tc("actions.loading")}</p>
            ) : previewJobs.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("jobs.empty")}</p>
            ) : (
              previewJobs.map((job) => (
                <div
                  key={job.job_id}
                  className="flex items-center justify-between gap-3 rounded-md bg-muted/30 px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs">{job.job_id}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {job.agent ? displayAgentName(job.agent) : "—"}
                    </p>
                  </div>
                  <Badge variant={statusVariant(job.status)}>{job.status}</Badge>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="dashboard-surface border-border/60 shadow-none">
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <CardTitle className="text-sm font-semibold">{t("chats.title")}</CardTitle>
          <Button variant="ghost" size="sm" className="h-8 text-xs" asChild>
            <Link to="/chat">{t("chats.openChat")}</Link>
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {previewTabs.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("chats.empty")}</p>
          ) : (
            previewTabs.map((tab) => (
              <div
                key={tab.id}
                className="flex items-center justify-between gap-3 rounded-md bg-muted/30 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{displayAgentName(tab.agent)}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {tab.harness} · {tab.model}
                  </p>
                </div>
                <Button variant="secondary" size="sm" className="h-8 shrink-0 text-xs" asChild>
                  <Link to="/chat">{tc("actions.open")}</Link>
                </Button>
              </div>
            ))
          )}
          {agents.length > 0 && previewTabs.length === 0 ? (
            <Button size="sm" asChild>
              <Link to="/chat">{t("chats.openChat")}</Link>
            </Button>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
