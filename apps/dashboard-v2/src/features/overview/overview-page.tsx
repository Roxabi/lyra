import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, Bot, Briefcase, MessageCircle } from "lucide-react";
import { useMemo } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetchAgentStatus, fetchAgents, fetchJobs, fetchOpsHealth } from "@/features/overview/api";
import type { AgentHealth } from "@/shared/api/bff-types";
import { AgentIdentity } from "@/shared/components/agent-identity";
import { ListRowsSkeleton, TableRowsSkeleton } from "@/shared/components/data-table";
import { EmptyState } from "@/shared/components/empty-state";
import { JobStatusBadge } from "@/shared/components/job-status-badge";
import { PageIntro } from "@/shared/components/page-intro";
import { displayAgentName } from "@/shared/lib/agents";
import type { HarnessKind } from "@/shared/lib/chats-storage";
import { loadTabs } from "@/shared/lib/chats-storage";

export function OverviewPage() {
  const tabs = loadTabs();

  const {
    data: agents = [],
    isError: agentsError,
    isLoading: agentsLoading,
  } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const {
    data: status = [],
    isLoading: statusLoading,
    isError: statusError,
  } = useQuery({
    queryKey: ["agent-status-all"],
    queryFn: () => fetchAgentStatus(),
    refetchInterval: 30_000,
  });
  const {
    data: jobs = [],
    isLoading: jobsLoading,
    isError: jobsError,
  } = useQuery({
    queryKey: ["jobs-overview"],
    queryFn: fetchJobs,
    refetchInterval: 15_000,
  });
  const {
    data: engines = [],
    isError: enginesError,
    isLoading: enginesLoading,
  } = useQuery({
    queryKey: ["ops-health-overview"],
    queryFn: fetchOpsHealth,
    refetchInterval: 30_000,
  });

  const offlineAgents = status.filter((s) => !s.online).length;
  const enginesDown = engines.filter((e) => !e.reachable).length;
  const alertParts: string[] = [];

  if (offlineAgents > 0) {
    alertParts.push(`${offlineAgents} agent${offlineAgents === 1 ? "" : "s"} offline`);
  }
  if (jobs.length > 0) {
    alertParts.push(`${jobs.length} active job${jobs.length === 1 ? "" : "s"}`);
  }
  if (enginesDown > 0) {
    alertParts.push(`${enginesDown} engine${enginesDown === 1 ? "" : "s"} down`);
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
      <PageIntro>
        Factory operator overview — agents, jobs, and platform health at a glance.
      </PageIntro>

      <section
        className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 bg-card px-4 py-3"
        aria-live="polite"
      >
        <AlertTriangle className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <p className="text-sm">
          {alertParts.length > 0 ? (
            <span className="font-medium text-foreground">{alertParts.join(" · ")}</span>
          ) : (
            <span className="text-muted-foreground">All systems nominal.</span>
          )}
        </p>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-medium">Agents</CardTitle>
            <Badge variant="outline" className="tabular-nums">
              {rosterAgents.length}
            </Badge>
          </CardHeader>
          <CardContent className="px-0 pb-0">
            {agentsError || statusError ? (
              <p className="px-4 pb-4 text-sm text-destructive" role="alert">
                Failed to load agent roster.
              </p>
            ) : null}
            {!agentsError && !statusError && (statusLoading || agentsLoading) ? (
              <TableRowsSkeleton rows={3} cols={3} />
            ) : null}
            {!agentsError &&
            !statusError &&
            !statusLoading &&
            !agentsLoading &&
            rosterAgents.length === 0 ? (
              <div className="px-4 pb-4">
                <EmptyState
                  icon={Bot}
                  title="No agents"
                  description="Configure agents to see roster status here."
                  className="py-8"
                />
              </div>
            ) : null}
            {!agentsError &&
            !statusError &&
            !statusLoading &&
            !agentsLoading &&
            rosterAgents.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-6">Agent</TableHead>
                    <TableHead>Harness</TableHead>
                    <TableHead className="pr-6">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rosterAgents.map((s) => (
                    <TableRow key={s.agent}>
                      <TableCell className="pl-6">
                        <AgentIdentity agentId={s.agent} avatarSize="sm" />
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{s.harness}</TableCell>
                      <TableCell className="pr-6">
                        <Badge variant={s.online ? "default" : "destructive"}>
                          {s.online ? "Online" : "Offline"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm font-medium">Jobs</CardTitle>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="tabular-nums">
                {jobs.length}
              </Badge>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 text-xs"
                render={<Link to="/jobs" />}
              >
                View all
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {jobsError ? (
              <p className="text-sm text-destructive" role="alert">
                Failed to load jobs.
              </p>
            ) : null}
            {!jobsError && jobsLoading ? (
              <ListRowsSkeleton rows={Math.max(jobs.length, 3)} />
            ) : null}
            {!jobsError && !jobsLoading && jobs.length === 0 ? (
              <EmptyState
                icon={Briefcase}
                title="No active jobs"
                description="Launch a job from the Jobs page."
                className="py-8"
              />
            ) : null}
            {!jobsError && !jobsLoading && jobs.length > 0
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
                    <JobStatusBadge status={job.status} />
                  </div>
                ))
              : null}
          </CardContent>
        </Card>
      </div>

      {enginesError ? (
        <p className="text-sm text-destructive" role="alert">
          Failed to load ops engine health.
        </p>
      ) : enginesLoading ? (
        <p className="text-sm text-muted-foreground">Loading ops health…</p>
      ) : null}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-sm font-medium">Chat tabs</CardTitle>
          <Button variant="ghost" size="sm" className="h-8 text-xs" render={<Link to="/chat" />}>
            Open chat
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {previewTabs.length === 0 ? (
            <EmptyState
              icon={MessageCircle}
              title="No chat tabs"
              description="Open chat to start a session with an agent."
              action={
                agents.length > 0 ? (
                  <Button size="sm" render={<Link to="/chat" />}>
                    Open chat
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
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-8 shrink-0 text-xs"
                  render={<Link to="/chat" />}
                >
                  Open
                </Button>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
