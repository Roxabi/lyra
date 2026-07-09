import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { fetchSpans, type SpanRow } from "@/features/spans/api";
import { PageIntro } from "@/shared/components/page-intro";

export function SpansPage() {
  const { t } = useTranslation("spans");
  const { t: tc } = useTranslation("common");
  const [poolId, setPoolId] = useState("");
  const [jobId, setJobId] = useState("");
  const [component, setComponent] = useState("");
  const [selected, setSelected] = useState<SpanRow | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["bff-spans", poolId, jobId, component],
    queryFn: () =>
      fetchSpans({
        pool_id: poolId || undefined,
        job_id: jobId || undefined,
        component: component || undefined,
      }),
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>{t("subtitle")}</PageIntro>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-2">
          <Label htmlFor="pool-id">{t("filters.poolId")}</Label>
          <Input
            id="pool-id"
            placeholder={t("filters.poolId")}
            value={poolId}
            onChange={(e) => setPoolId(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="job-id">{t("filters.jobId")}</Label>
          <Input
            id="job-id"
            placeholder={t("filters.jobId")}
            value={jobId}
            onChange={(e) => setJobId(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="component">{t("filters.component")}</Label>
          <Input
            id="component"
            placeholder={t("filters.component")}
            value={component}
            onChange={(e) => setComponent(e.target.value)}
          />
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">
            {data ? t("titleCount", { count: data.total }) : t("title")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading && <p className="text-sm text-muted-foreground">{tc("actions.loading")}</p>}
          {isError && (
            <p className="text-sm text-destructive" role="alert">
              {t("loadError")}
            </p>
          )}
          {data && data.items.length === 0 && (
            <p className="text-sm text-muted-foreground">{t("empty")}</p>
          )}
          {data && data.items.length > 0 && (
            <ul className="divide-y text-sm">
              {data.items.map((row) => (
                <li key={`${row.trace_id}:${row.span_id}`}>
                  <button
                    type="button"
                    className="w-full py-2 text-left hover:bg-muted/40"
                    onClick={() => setSelected(row)}
                  >
                    <span className="font-mono">{row.job_id ?? "—"}</span>
                    <span className="mx-2 text-muted-foreground">·</span>
                    <span>{row.component ?? row.name}</span>
                    <span className="mx-2 text-muted-foreground">·</span>
                    <span>{row.duration_ms.toFixed(1)} ms</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {selected ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">{t("rawJson")}</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="max-h-96 overflow-auto rounded bg-muted p-3 text-xs">
              {JSON.stringify(selected, null, 2)}
            </pre>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
