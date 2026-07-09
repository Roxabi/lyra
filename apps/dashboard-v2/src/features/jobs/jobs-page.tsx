import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TableCell, TableHead, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { cancelJob, fetchAgentStatus, fetchAgents, launchJob, steerJob } from "@/features/jobs/api";
import {
  DataTable,
  DataTableBody,
  DataTableHeader,
  TableRowsSkeleton,
} from "@/shared/components/data-table";
import { EmptyState } from "@/shared/components/empty-state";
import { FilterChip } from "@/shared/components/filter-chip";
import { JobStatusBadge } from "@/shared/components/job-status-badge";
import {
  ListToolbar,
  ListToolbarControls,
  ListToolbarHeader,
  ListToolbarSearch,
} from "@/shared/components/list-toolbar";
import { PageIntro } from "@/shared/components/page-intro";
import { SelectField } from "@/shared/components/select-field";
import { SortableTableHeader } from "@/shared/components/sortable-table-header";
import { useDebouncedValue } from "@/shared/hooks/use-debounced-value";
import { useJobsLive } from "@/shared/hooks/use-jobs-live";
import { displayAgentName } from "@/shared/lib/agents";
import {
  filterJobs,
  type JobsSortKey,
  sortJobs,
  uniqueJobStatuses,
} from "@/shared/lib/jobs-filters";
import { type SortDirection, toggleSort } from "@/shared/lib/sort";

export function JobsPage() {
  const { t } = useTranslation("jobs");
  const { t: tc } = useTranslation("common");
  const [launchAgent, setLaunchAgent] = useState("");
  const [launchPrompt, setLaunchPrompt] = useState("");
  const [steerTexts, setSteerTexts] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");
  const [statusFilters, setStatusFilters] = useState<string[]>([]);
  const [sortKey, setSortKey] = useState<JobsSortKey>("started_at");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const debouncedSearch = useDebouncedValue(search, 300);

  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const { data: status = [] } = useQuery({
    queryKey: ["agent-status-jobs"],
    queryFn: () => fetchAgentStatus(),
    refetchInterval: 15_000,
  });
  const { jobs, isLoading, isError } = useJobsLive();

  const selectedAgent = launchAgent || agents[0] || "";
  const statusOptions = useMemo(() => uniqueJobStatuses(jobs), [jobs]);
  const hasFilters = search.trim().length > 0 || statusFilters.length > 0;

  const visibleJobs = useMemo(() => {
    const filtered = filterJobs(jobs, { search: debouncedSearch, statuses: statusFilters });
    return sortJobs(filtered, sortKey, sortDirection);
  }, [jobs, debouncedSearch, statusFilters, sortKey, sortDirection]);

  const launchMutation = useMutation({
    mutationFn: () =>
      launchJob({
        agent: selectedAgent,
        prompt: launchPrompt.trim(),
        job_name: "omp",
      }),
    onSuccess: (res) => {
      if (res.accepted) {
        toast.info(t("launch.launched", { jobId: res.job_id }));
      } else {
        toast.error(res.message);
      }
      setLaunchPrompt("");
    },
    onError: () => toast.error(t("launch.launchFailed")),
  });

  const steerMutation = useMutation({
    mutationFn: ({ jobId, text }: { jobId: string; text: string }) => steerJob(jobId, text),
    onSuccess: (_res, vars) => {
      setSteerTexts((prev) => ({ ...prev, [vars.jobId]: "" }));
      toast.info(t("steer.sent", { jobId: vars.jobId }));
    },
    onError: () => toast.error(t("steer.failed")),
  });

  const cancelMutation = useMutation({
    mutationFn: (jobId: string) => cancelJob(jobId),
    onSuccess: (_res, jobId) => toast.info(t("cancel.sent", { jobId })),
    onError: () => toast.error(t("cancel.failed")),
  });

  function onSort(nextKey: JobsSortKey) {
    const next = toggleSort(sortKey, sortDirection, nextKey);
    setSortKey(next.key);
    setSortDirection(next.direction);
  }

  function toggleStatusFilter(statusValue: string) {
    setStatusFilters((prev) =>
      prev.includes(statusValue) ? prev.filter((s) => s !== statusValue) : [...prev, statusValue],
    );
  }

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>{t("subtitle")}</PageIntro>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">{t("launch.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <SelectField
              label={t("launch.agentLabel")}
              value={selectedAgent}
              options={agents.map((a) => ({
                value: a,
                label: displayAgentName(a),
              }))}
              onChange={setLaunchAgent}
              className="min-w-[12rem] flex-1"
            />
            <Badge variant="outline">factory.jobs.omp</Badge>
          </div>
          <div className="space-y-2">
            <Label htmlFor="launch-prompt">{t("launch.promptLabel")}</Label>
            <Textarea
              id="launch-prompt"
              value={launchPrompt}
              onChange={(e) => setLaunchPrompt(e.target.value)}
              placeholder={t("launch.promptPlaceholder")}
              rows={3}
            />
          </div>
          <Button
            type="button"
            className="self-start"
            disabled={!launchPrompt.trim() || !selectedAgent || launchMutation.isPending}
            onClick={() => launchMutation.mutate()}
          >
            {launchMutation.isPending ? t("launch.submitting") : t("launch.submit")}
          </Button>
        </CardContent>
      </Card>

      <div className="space-y-4">
        <ListToolbar>
          <ListToolbarHeader
            meta={
              isLoading ? tc("actions.loading") : t("live.active", { count: visibleJobs.length })
            }
          />
          <ListToolbarSearch
            value={search}
            onChange={setSearch}
            placeholder={t("searchPlaceholder")}
            aria-label={tc("search")}
          />
          {statusOptions.length > 0 ? (
            <ListToolbarControls
              filters={
                <>
                  <span className="text-xs text-muted-foreground">{t("filters.status")}</span>
                  {statusOptions.map((statusValue) => (
                    <FilterChip
                      key={statusValue}
                      label={statusValue}
                      active={statusFilters.includes(statusValue)}
                      onToggle={() => toggleStatusFilter(statusValue)}
                    />
                  ))}
                </>
              }
            />
          ) : null}
        </ListToolbar>

        {isLoading ? <TableRowsSkeleton rows={3} cols={8} /> : null}

        {isError ? (
          <p className="text-sm text-destructive" role="alert">
            {t("live.loadError")}
          </p>
        ) : null}

        {!isLoading && !isError && visibleJobs.length === 0 ? (
          <EmptyState
            title={hasFilters ? t("live.emptyFiltered") : t("live.empty")}
            description={hasFilters ? undefined : t("live.emptyHint")}
          />
        ) : null}

        {!isLoading && !isError && visibleJobs.length > 0 ? (
          <DataTable>
            <DataTableHeader>
              <SortableTableHeader
                label={t("table.job")}
                active={sortKey === "job_id"}
                direction={sortDirection}
                onClick={() => onSort("job_id")}
              />
              <SortableTableHeader
                label={t("table.agent")}
                active={sortKey === "agent"}
                direction={sortDirection}
                onClick={() => onSort("agent")}
              />
              <TableHead>{t("table.platform")}</TableHead>
              <SortableTableHeader
                label={t("table.status")}
                active={sortKey === "status"}
                direction={sortDirection}
                onClick={() => onSort("status")}
              />
              <TableHead>{t("table.mode")}</TableHead>
              <SortableTableHeader
                label={t("table.started")}
                active={sortKey === "started_at"}
                direction={sortDirection}
                onClick={() => onSort("started_at")}
              />
              <TableHead>{t("table.steer")}</TableHead>
              <TableHead>{t("table.actions")}</TableHead>
            </DataTableHeader>
            <DataTableBody>
              {visibleJobs.map((job) => (
                <TableRow key={job.job_id}>
                  <TableCell>
                    <p className="font-mono text-xs">{job.job_id}</p>
                    <p className="truncate text-[10px] text-muted-foreground">{job.pool_id}</p>
                  </TableCell>
                  <TableCell>{job.agent ? displayAgentName(job.agent) : "—"}</TableCell>
                  <TableCell className="capitalize">{job.platform ?? "—"}</TableCell>
                  <TableCell>
                    <JobStatusBadge status={job.status} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {job.concurrency_mode}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground tabular-nums">
                    {new Date(job.started_at).toLocaleString()}
                  </TableCell>
                  <TableCell>
                    <div className="flex min-w-48 items-center gap-2">
                      <Input
                        size={1}
                        className="h-8 text-xs"
                        value={steerTexts[job.job_id] ?? ""}
                        onChange={(e) =>
                          setSteerTexts((prev) => ({
                            ...prev,
                            [job.job_id]: e.target.value,
                          }))
                        }
                        placeholder={t("table.steerPlaceholder")}
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        className="h-8 shrink-0 px-2 text-xs"
                        disabled={!(steerTexts[job.job_id] ?? "").trim() || steerMutation.isPending}
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
                  </TableCell>
                  <TableCell>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      className="h-8 text-xs"
                      disabled={cancelMutation.isPending}
                      onClick={() => cancelMutation.mutate(job.job_id)}
                    >
                      {t("table.cancel")}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </DataTableBody>
          </DataTable>
        ) : null}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">{t("workers.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {agents.map((agent) => {
            const health = status.find((s) => s.agent === agent);
            return (
              <div
                key={agent}
                className="flex items-center justify-between rounded-lg bg-muted/30 px-3 py-2.5"
              >
                <div>
                  <p className="text-sm font-medium">{displayAgentName(agent)}</p>
                  <p className="text-xs text-muted-foreground">
                    Harness {health?.harness ?? "claude-cli"}
                  </p>
                </div>
                <Badge variant={health?.online ? "default" : "outline"}>
                  {health?.online ? tc("status.active") : tc("status.inactive")}
                </Badge>
              </div>
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
}
