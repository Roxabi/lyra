import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { PageIntro } from "@/components/layout/PageIntro";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { usePipelineRuns } from "@/hooks/usePipelineRuns";
import type { PipelineRun, PipelineStageStatus } from "@/lib/api";
import { filterPipelineRuns, isPipelineRowStale, type PipelineFilter } from "@/lib/pipeline";

const FILTER_OPTIONS: PipelineFilter[] = ["ci_red", "awaiting_reviewed", "deploy_pending"];

function stageVariant(
  status: PipelineStageStatus,
): "success" | "destructive" | "secondary" | "outline" {
  if (status === "success") return "success";
  if (status === "failure") return "destructive";
  if (status === "running" || status === "pending") return "secondary";
  return "outline";
}

function StageBadge({ label, status }: { label: string; status: PipelineStageStatus }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <Badge variant={stageVariant(status)} className="w-fit text-xs">
        {t(`pipeline.stage.${status}`)}
      </Badge>
    </div>
  );
}

function formatLastEvent(iso: string | null): string {
  if (!iso) return "—";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "—";
  const ageMin = Math.round((Date.now() - ts) / 60_000);
  if (ageMin < 1) return "<1m";
  if (ageMin < 60) return `${ageMin}m`;
  return `${Math.round(ageMin / 60)}h`;
}

export function PipelinePage() {
  const { t } = useTranslation("dashboard");
  const { runs, isError, isLoading } = usePipelineRuns();
  const [activeFilters, setActiveFilters] = useState<Set<PipelineFilter>>(new Set());

  const visibleRuns = useMemo(() => filterPipelineRuns(runs, activeFilters), [runs, activeFilters]);

  function toggleFilter(filter: PipelineFilter) {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(filter)) next.delete(filter);
      else next.add(filter);
      return next;
    });
  }

  return (
    <div className="space-y-6">
      <PageIntro>{t("pipeline.subtitle")}</PageIntro>

      <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm text-muted-foreground">
        {t("pipeline.disclaimer")}
      </p>

      {isError ? <p className="text-sm text-destructive">{t("pipeline.loadError")}</p> : null}

      <Card className="dashboard-surface border-border/60 shadow-none">
        <CardHeader className="space-y-3 pb-2">
          <CardTitle className="text-sm text-muted-foreground">
            {t("pipeline.tableTitle")}
          </CardTitle>
          <div className="flex flex-wrap gap-2">
            {FILTER_OPTIONS.map((filter) => {
              const active = activeFilters.has(filter);
              return (
                <Button
                  key={filter}
                  type="button"
                  size="sm"
                  variant={active ? "default" : "outline"}
                  onClick={() => toggleFilter(filter)}
                >
                  {t(`pipeline.filters.${filter}`)}
                </Button>
              );
            })}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoading ? (
            <p className="py-4 text-sm text-muted-foreground">{t("pipeline.loading")}</p>
          ) : visibleRuns.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">
              {runs.length === 0 ? t("pipeline.empty") : t("pipeline.noMatches")}
            </p>
          ) : (
            visibleRuns.map((row: PipelineRun) => (
              <div
                key={`${row.repo}:${row.pr_number}`}
                className="rounded-lg border border-border/50 bg-background/40 p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-mono text-xs text-muted-foreground">
                      #{row.pr_number}
                      {row.head_ref ? ` · ${row.head_ref}` : ""}
                    </p>
                    {row.html_url ? (
                      <a
                        href={row.html_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sm font-medium text-foreground hover:underline"
                      >
                        {row.title}
                      </a>
                    ) : (
                      <p className="text-sm font-medium">{row.title}</p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {isPipelineRowStale(row.last_event_at) ? (
                      <Badge variant="destructive">{t("pipeline.stale")}</Badge>
                    ) : null}
                    {row.reviewed ? (
                      <Badge variant="success">{t("pipeline.reviewed")}</Badge>
                    ) : (
                      <Badge variant="outline">{t("pipeline.notReviewed")}</Badge>
                    )}
                    {!row.open ? <Badge variant="secondary">{t("pipeline.merged")}</Badge> : null}
                  </div>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                  <StageBadge label={t("pipeline.columns.ci")} status={row.ci_status} />
                  <StageBadge label={t("pipeline.columns.merge")} status={row.merge_status} />
                  <StageBadge label={t("pipeline.columns.publish")} status={row.publish_status} />
                  <StageBadge label={t("pipeline.columns.m1")} status={row.m1_deploy_status} />
                  <StageBadge label={t("pipeline.columns.cf")} status={row.cf_deploy_status} />
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  {t("pipeline.lastEvent", { age: formatLastEvent(row.last_event_at) })}
                </p>
                {row.checks.length > 0 ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {row.checks.map((c) => `${c.name}:${c.conclusion ?? c.status}`).join(" · ")}
                  </p>
                ) : null}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
