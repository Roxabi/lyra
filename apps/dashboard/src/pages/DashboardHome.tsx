import { Badge } from "@astryxdesign/core/Badge";
import { Card } from "@astryxdesign/core/Card";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import { Stack } from "@astryxdesign/core/Stack";
import {
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
} from "@astryxdesign/core/Table";
import { Text } from "@astryxdesign/core/Text";
import { Briefcase, ChatCircleDots, Robot, Warning } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { AgentIdentity } from "@/components/agents/AgentIdentity";
import { PageIntro } from "@/components/layout/PageIntro";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
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
          <Skeleton width={32} height={32} radius="rounded" />
          <Skeleton width={96} height={16} />
          <Skeleton width={64} height={24} radius="rounded" className="ml-auto" />
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
          <Skeleton width={112} height={16} />
          <Skeleton width={56} height={24} radius="rounded" className="ml-auto" />
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
        <Card padding={0}>
          <Stack gap={4}>
            <Stack direction="horizontal" justify="between" align="center" className="px-6 pt-6">
              <Text type="label" as="h3">
                {t("agents.title")}
              </Text>
              <Badge variant="neutral" className="tabular-nums" label={rosterAgents.length} />
            </Stack>
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
                <Table dividers="rows" hasHover>
                  <TableHeader>
                    <TableRow isHeaderRow>
                      {/* pl-6/pr-6 realign the edge columns with the Card's
                          px-6 title row — Astryx edge-compensation collapses to
                          8px under Card padding={0} (--container-padding-*=0). */}
                      <TableHeaderCell scope="col" className="pl-6">
                        {t("agents.colAgent")}
                      </TableHeaderCell>
                      <TableHeaderCell scope="col">{t("agents.colHarness")}</TableHeaderCell>
                      <TableHeaderCell scope="col" className="pr-6">
                        {t("agents.colStatus")}
                      </TableHeaderCell>
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
                          <Badge
                            variant={s.online ? "success" : "error"}
                            label={s.online ? tc("status.online") : tc("status.offline")}
                          />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : null}
          </Stack>
        </Card>

        <Card>
          <Stack gap={4}>
            <Stack direction="horizontal" justify="between" align="center">
              <Text type="label" as="h3">
                {t("jobs.title")}
              </Text>
              <div className="flex items-center gap-2">
                <Badge variant="neutral" className="tabular-nums" label={jobs.length} />
                <Button variant="ghost" size="sm" className="h-8 text-xs" href="/jobs">
                  {tc("actions.viewAll")}
                </Button>
              </div>
            </Stack>
            <Stack gap={2}>
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
                      <Badge variant={jobStatusToBadgeVariant(job.status)} label={job.status} />
                    </div>
                  ))
                : null}
            </Stack>
          </Stack>
        </Card>
      </div>

      <Card>
        <Stack gap={4}>
          <Stack direction="horizontal" justify="between" align="center">
            <Text type="label" as="h3">
              {t("chats.title")}
            </Text>
            <Button variant="ghost" size="sm" className="h-8 text-xs" href="/chat">
              {t("chats.openChat")}
            </Button>
          </Stack>
          <Stack gap={2}>
            {previewTabs.length === 0 ? (
              <EmptyState
                icon={ChatCircleDots}
                title={t("chats.empty")}
                hint={t("chats.emptyHint")}
                action={
                  agents.length > 0 ? (
                    <Button size="sm" href="/chat">
                      {t("chats.openChat")}
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
                    href="/chat"
                  >
                    {tc("actions.open")}
                  </Button>
                </div>
              ))
            )}
          </Stack>
        </Stack>
      </Card>
    </div>
  );
}
