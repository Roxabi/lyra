import { Badge } from "@astryxdesign/core/Badge";
import { Card } from "@astryxdesign/core/Card";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import { Stack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { TextArea } from "@astryxdesign/core/TextArea";
import { TextInput } from "@astryxdesign/core/TextInput";
import { Briefcase } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { PageIntro } from "@/components/layout/PageIntro";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { FilterChip } from "@/components/ui/filter-chip";
import {
  ListToolbar,
  ListToolbarControls,
  ListToolbarHeader,
  ListToolbarSearch,
} from "@/components/ui/list-toolbar";
import { PopoverSelect } from "@/components/ui/popover-select";
import { toast } from "@/components/ui/sonner";
import { SortableTableHeader } from "@/components/ui/sortable-table-header";
import { displayAgentName } from "@/lib/agents";
import {
  cancelJob,
  fetchAgentStatus,
  fetchAgents,
  fetchJobs,
  launchJob,
  steerJob,
} from "@/lib/api";
import { jobStatusToBadgeVariant } from "@/lib/job-status";
import { filterJobs, type JobsSortKey, sortJobs, uniqueJobStatuses } from "@/lib/jobs-filters";
import { type SortDirection, toggleSort } from "@/lib/sort";
import { useDebouncedValue } from "@/lib/use-debounced-value";

export function JobsPage() {
  const { t } = useTranslation("jobs");
  const { t: tc } = useTranslation("common");
  const queryClient = useQueryClient();
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
        toast.success(t("launch.launched", { jobId: res.job_id }));
      } else {
        toast.error(res.message);
      }
      setLaunchPrompt("");
      void queryClient.invalidateQueries({ queryKey: ["jobs-live"] });
    },
    onError: () => toast.error(t("launch.launchFailed")),
  });

  const steerMutation = useMutation({
    mutationFn: ({ jobId, text }: { jobId: string; text: string }) => steerJob(jobId, text),
    onSuccess: (_res, vars) => {
      setSteerTexts((prev) => ({ ...prev, [vars.jobId]: "" }));
      toast.success(t("steer.sent", { jobId: vars.jobId }));
    },
    onError: () => toast.error(t("steer.failed")),
  });

  const cancelMutation = useMutation({
    mutationFn: (jobId: string) => cancelJob(jobId),
    onSuccess: (_res, jobId) => {
      toast.success(t("cancel.sent", { jobId }));
      void queryClient.invalidateQueries({ queryKey: ["jobs-live"] });
    },
    onError: () => toast.error(t("cancel.failed")),
  });

  function onSort(nextKey: JobsSortKey) {
    const next = toggleSort(sortKey, sortDirection, nextKey);
    setSortKey(next.key);
    setSortDirection(next.direction);
  }

  function toggleStatusFilter(status: string) {
    setStatusFilters((prev) =>
      prev.includes(status) ? prev.filter((s) => s !== status) : [...prev, status],
    );
  }

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>{t("subtitle")}</PageIntro>

      <Card>
        <Stack gap={4}>
          <Text type="label" as="h3">
            {t("launch.title")}
          </Text>
          <Stack gap={3}>
            <div className="flex flex-wrap items-center gap-3">
              <PopoverSelect
                label={t("launch.agentLabel")}
                value={selectedAgent}
                options={agents.map((a) => ({
                  value: a,
                  label: displayAgentName(a),
                }))}
                onChange={setLaunchAgent}
              />
              <Badge variant="neutral" label="factory.jobs.omp" />
            </div>
            <TextArea
              label={t("launch.promptLabel")}
              isLabelHidden
              value={launchPrompt}
              onChange={(next) => setLaunchPrompt(next)}
              placeholder={t("launch.promptPlaceholder")}
              rows={3}
            />
            <Button
              type="button"
              className="self-start"
              disabled={!launchPrompt.trim() || !selectedAgent || launchMutation.isPending}
              onClick={() => launchMutation.mutate()}
            >
              {launchMutation.isPending ? t("launch.submitting") : t("launch.submit")}
            </Button>
          </Stack>
        </Stack>
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
                  {statusOptions.map((status) => (
                    <FilterChip
                      key={status}
                      active={statusFilters.includes(status)}
                      onClick={() => toggleStatusFilter(status)}
                    >
                      {status}
                    </FilterChip>
                  ))}
                </>
              }
            />
          ) : null}
        </ListToolbar>

        {isLoading ? (
          <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
            <div
              role="status"
              className="divide-y divide-border/30 px-4"
              aria-busy="true"
              aria-label={tc("actions.loading")}
            >
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex items-center gap-3 py-3">
                  <Skeleton width={112} height={16} />
                  <Skeleton width={64} height={16} />
                  <Skeleton width={56} height={16} />
                  <Skeleton width={64} height={24} radius="rounded" />
                  <Skeleton width={48} height={16} />
                  <Skeleton width={80} height={16} />
                  <Skeleton width={56} height={28} radius={2} className="ml-auto" />
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {isError ? (
          <p className="text-sm text-destructive" role="alert">
            {t("live.loadError")}
          </p>
        ) : null}

        {!isLoading && !isError && visibleJobs.length === 0 ? (
          <EmptyState
            icon={hasFilters ? undefined : Briefcase}
            title={hasFilters ? t("live.emptyFiltered") : t("live.empty")}
            hint={hasFilters ? undefined : t("live.emptyHint")}
          />
        ) : null}

        {!isLoading && !isError && visibleJobs.length > 0 ? (
          <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead>
                <tr className="border-b border-border/50 text-xs">
                  <SortableTableHeader
                    label={t("table.job")}
                    active={sortKey === "job_id"}
                    direction={sortDirection}
                    onClick={() => onSort("job_id")}
                    className="px-4 py-2"
                  />
                  <SortableTableHeader
                    label={t("table.agent")}
                    active={sortKey === "agent"}
                    direction={sortDirection}
                    onClick={() => onSort("agent")}
                  />
                  <th className="py-2 pr-3 font-medium text-muted-foreground">
                    {t("table.platform")}
                  </th>
                  <SortableTableHeader
                    label={t("table.status")}
                    active={sortKey === "status"}
                    direction={sortDirection}
                    onClick={() => onSort("status")}
                  />
                  <th className="py-2 pr-3 font-medium text-muted-foreground">{t("table.mode")}</th>
                  <SortableTableHeader
                    label={t("table.started")}
                    active={sortKey === "started_at"}
                    direction={sortDirection}
                    onClick={() => onSort("started_at")}
                  />
                  <th className="py-2 pr-3 font-medium text-muted-foreground">
                    {t("table.steer")}
                  </th>
                  <th className="py-2 pr-4 font-medium text-muted-foreground">
                    {t("table.actions")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleJobs.map((job) => (
                  <tr
                    key={job.job_id}
                    className="border-b border-border/30 transition-colors last:border-0 hover:bg-muted/15"
                  >
                    <td className="px-4 py-2.5 pr-3">
                      <p className="font-mono text-xs">{job.job_id}</p>
                      <p className="truncate text-[10px] text-muted-foreground">{job.pool_id}</p>
                    </td>
                    <td className="py-2.5 pr-3">{job.agent ? displayAgentName(job.agent) : "—"}</td>
                    <td className="py-2.5 pr-3 capitalize">{job.platform ?? "—"}</td>
                    <td className="py-2.5 pr-3">
                      <Badge variant={jobStatusToBadgeVariant(job.status)} label={job.status} />
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-muted-foreground">
                      {job.concurrency_mode}
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-muted-foreground tabular-nums">
                      {new Date(job.started_at).toLocaleString()}
                    </td>
                    <td className="py-2.5 pr-3">
                      <div className="flex min-w-[12rem] items-center gap-2">
                        <TextInput
                          label={t("table.steerPlaceholder")}
                          isLabelHidden
                          size="sm"
                          value={steerTexts[job.job_id] ?? ""}
                          onChange={(v) =>
                            setSteerTexts((prev) => ({
                              ...prev,
                              [job.job_id]: v,
                            }))
                          }
                          placeholder={t("table.steerPlaceholder")}
                          width="100%"
                        />
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          className="h-8 shrink-0 px-2 text-xs active:scale-[0.98]"
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
                    <td className="py-2.5 pr-4">
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        className="h-8 text-xs active:scale-[0.98]"
                        disabled={cancelMutation.isPending}
                        onClick={() => cancelMutation.mutate(job.job_id)}
                      >
                        {t("table.cancel")}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      <Card>
        <Stack gap={4}>
          <Text type="label" as="h3">
            {t("workers.title")}
          </Text>
          <Stack gap={2}>
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
                  <Badge
                    variant={health?.online ? "success" : "neutral"}
                    label={health?.online ? tc("status.active") : tc("status.inactive")}
                  />
                </div>
              );
            })}
          </Stack>
        </Stack>
      </Card>
    </div>
  );
}
