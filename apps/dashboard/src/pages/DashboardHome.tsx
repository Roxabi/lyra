import { Briefcase, ChatCircleDots, Robot, Warning } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { AgentIdentity } from "@/components/agents/AgentIdentity";
import { PageIntro } from "@/components/layout/PageIntro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { displayAgentName } from "@/lib/agents";
import {
  type AgentHealth,
  fetchAgentStatus,
  fetchAgents,
  fetchJobs,
  fetchOpsHealth,
} from "@/lib/api";
import { type HarnessKind, loadTabs } from "@/lib/chats-storage";
import { jobStatusToBadgeVariant } from "@/lib/job-status";

const SKELETON_ROW_IDS = ["alpha", "beta", "gamma", "delta"] as const;

function TableRowsSkeleton({ rows = 3 }: { rows?: number }) {
  const { t } = useTranslation("common");
  return (
    <div
      role="status"
      className="space-y-2 px-6 pb-6"
      aria-busy="true"
      aria-label={t("actions.loading")}
    >
      {SKELETON_ROW_IDS.slice(0, rows).map((id) => (
        <div key={id} className="flex items-center gap-3 py-1">
          <Skeleton className="size-8 rounded-full" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="ml-auto h-6 w-16 rounded-full" />
        </div>
      ))}
    </div>
  );
}

function ListRowsSkeleton({ rows = 3 }: { rows?: number }) {
  const { t } = useTranslation("common");
  return (
    <div role="status" className="space-y-2" aria-busy="true" aria-label={t("actions.loading")}>
      {SKELETON_ROW_IDS.slice(0, rows).map((id) => (
        <div key={id} className="flex items-center gap-3 rounded-md bg-muted/30 px-3 py-2">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="ml-auto h-6 w-14 rounded-full" />
        </div>
      ))}
    </div>
  );
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

  const rosterAgents = useMemo((): AgentHealth[] => {
    const statusByAgent = new Map(status.map((row) => [row.agent, row]));
    const names = agents.length > 0 ? agents : status.map((row) => row.agent);
    return names.map(
      (name) =>
        statusByAgent.get(name) ?? {
          agent: name,
          in_roster: true,
          harness: "claude-cli" as HarnessKind,
          harness_reachable: false,
          online: false,
        },
    );
  }, [agents, status]);

  const previewTabs = tabs.slice(0, 3);

  return (
    <div className="space-y-6 pb-8">
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
              {rosterAgents.length}
            </Badge>
          </CardHeader>
          <CardContent className="p-0">
            {statusLoading ? <TableRowsSkeleton /> : null}
            {!statusLoading && rosterAgents.length === 0 ? (
              <div className="px-4 pb-4">
                <EmptyState
                  icon={Robot}
                  title={t("agents.empty")}
                  hint={t("agents.emptyHint")}
                  className="py-8"
                />
              </div>
            ) : null}
            {!statusLoading && rosterAgents.length > 0 ? (
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
                    {rosterAgents.map((s) => (
                      <tr
                        key={s.agent}
                        className="border-t border-border/30 transition-colors hover:bg-muted/15"
                      >
                        <td className="px-6 py-2.5">
                          <AgentIdentity agentId={s.agent} avatarSize="sm" />
                        </td>
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
            ) : null}
          </CardContent>
        </Card>

        <Card className="dashboard-surface border-border/60 shadow-none">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
            <CardTitle className="text-sm font-semibold">{t("jobs.title")}</CardTitle>
            <div className="flex items-center gap-2">
              <Badge variant="secondary" className="tabular-nums">
                {jobs.length}
              </Badge>
              <Button variant="ghost" size="sm" className="h-8 text-xs" asChild>
                <Link to="/jobs">{tc("actions.viewAll")}</Link>
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {jobsLoading ? <ListRowsSkeleton rows={Math.max(jobs.length, 3)} /> : null}
            {!jobsLoading && jobs.length === 0 ? (
              <EmptyState
                icon={Briefcase}
                title={t("jobs.empty")}
                hint={t("jobs.emptyHint")}
                className="py-8"
              />
            ) : null}
            {!jobsLoading && jobs.length > 0
              ? jobs.map((job) => (
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
                    <Badge variant={jobStatusToBadgeVariant(job.status)}>{job.status}</Badge>
                  </div>
                ))
              : null}
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
            <EmptyState
              icon={ChatCircleDots}
              title={t("chats.empty")}
              hint={t("chats.emptyHint")}
              action={
                agents.length > 0 ? (
                  <Button size="sm" asChild>
                    <Link to="/chat">{t("chats.openChat")}</Link>
                  </Button>
                ) : undefined
              }
              className="py-8"
            />
          ) : (
            previewTabs.map((tab) => (
              <div
                key={tab.id}
                className="flex items-center justify-between gap-3 rounded-md bg-muted/30 px-3 py-2"
              >
                <AgentIdentity
                  agentId={tab.agent}
                  avatarSize="sm"
                  subtitle={`${tab.harness} · ${tab.model}`}
                />
                <Button variant="secondary" size="sm" className="h-8 shrink-0 text-xs" asChild>
                  <Link to="/chat">{tc("actions.open")}</Link>
                </Button>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
