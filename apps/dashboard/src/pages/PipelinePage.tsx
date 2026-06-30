import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { PageIntro } from "@/components/layout/PageIntro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type PipelineRun, type PipelineStageStatus, fetchPipeline } from "@/lib/api";

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

export function PipelinePage() {
  const { t } = useTranslation("dashboard");
  const { data: runs = [], isError } = useQuery({
    queryKey: ["pipeline"],
    queryFn: fetchPipeline,
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-6">
      <PageIntro>{t("pipeline.subtitle")}</PageIntro>

      <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm text-muted-foreground">
        {t("pipeline.disclaimer")}
      </p>

      {isError ? (
        <p className="text-sm text-destructive">{t("pipeline.loadError")}</p>
      ) : null}

      <Card className="dashboard-surface border-border/60 shadow-none">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-muted-foreground">{t("pipeline.tableTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {runs.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">{t("pipeline.empty")}</p>
          ) : (
            runs.map((row: PipelineRun) => (
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
                  <div className="flex flex-wrap gap-2">
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
                {row.checks.length > 0 ? (
                  <p className="mt-3 text-xs text-muted-foreground">
                    {row.checks
                      .map((c) => `${c.name}:${c.conclusion ?? c.status}`)
                      .join(" · ")}
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