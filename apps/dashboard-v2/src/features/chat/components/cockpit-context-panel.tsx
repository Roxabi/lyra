import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { fetchJobs } from "@/features/overview/api";
import type { AgentHealth } from "@/shared/api/bff-types";
import { AgentIdentity } from "@/shared/components/agent-identity";
import { AgentStatusBadge } from "@/shared/components/agent-status-badge";
import { JobStatusBadge } from "@/shared/components/job-status-badge";

interface CockpitContextPanelProps {
  agent: string | null;
  health: AgentHealth | undefined;
}

export function CockpitContextPanel({ agent, health }: CockpitContextPanelProps) {
  const { t } = useTranslation("chat");
  const { t: tc } = useTranslation("common");
  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ["jobs-cockpit"],
    queryFn: fetchJobs,
    refetchInterval: 10_000,
  });

  const agentJobs = agent ? jobs.filter((j) => j.agent === agent).slice(0, 4) : jobs.slice(0, 4);

  return (
    <aside className="flex w-64 shrink-0 flex-col bg-card/40">
      <div className="px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("context.title")}
        </h2>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <section className="space-y-2">
          <h3 className="text-sm font-medium">{t("context.agentStatus")}</h3>
          {agent ? (
            <div className="rounded-lg border bg-background/50 px-3 py-2.5">
              <AgentIdentity agentId={agent} avatarSize="sm" />
              <div className="mt-2 flex flex-wrap items-center gap-2 pl-8">
                <AgentStatusBadge health={health} />
                {health?.harness ? (
                  <span className="text-xs text-muted-foreground">{health.harness}</span>
                ) : null}
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">—</p>
          )}
        </section>

        <section className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-medium">{t("context.activeJobs")}</h3>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              render={<Link to="/jobs" />}
            >
              {tc("actions.viewAll")}
            </Button>
          </div>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">{tc("actions.loading")}</p>
          ) : agentJobs.length === 0 ? (
            <p className="text-sm text-muted-foreground">—</p>
          ) : (
            <ul className="space-y-2">
              {agentJobs.map((job) => (
                <li key={job.job_id} className="rounded-md bg-muted/30 px-3 py-2">
                  <p className="truncate font-mono text-xs">{job.job_id}</p>
                  <JobStatusBadge status={job.status} className="mt-1" />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </aside>
  );
}
