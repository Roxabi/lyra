import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { PageIntro } from "@/components/layout/PageIntro";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  type FleetRow,
  type FleetStatus,
  type ImageDigestStatus,
  fetchFleet,
} from "@/lib/api";

function statusVariant(status: FleetStatus): "success" | "destructive" | "secondary" | "outline" {
  switch (status) {
    case "ok":
      return "success";
    case "stale":
      return "destructive";
    case "pinned":
      return "secondary";
    default:
      return "outline";
  }
}

function digestVariant(
  status: ImageDigestStatus,
): "success" | "destructive" | "secondary" | "outline" {
  switch (status) {
    case "current":
      return "success";
    case "stale":
      return "destructive";
    case "n/a":
      return "secondary";
    default:
      return "outline";
  }
}

function formatAge(ageS: number | null | undefined): string {
  if (ageS == null) return "—";
  if (ageS < 60) return `${Math.round(ageS)}s`;
  return `${Math.round(ageS / 60)}m`;
}

export function FleetPage() {
  const { t } = useTranslation("dashboard");
  const { data: rows = [], isError } = useQuery({
    queryKey: ["fleet"],
    queryFn: fetchFleet,
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-6">
      <PageIntro>{t("fleet.subtitle")}</PageIntro>

      {isError ? <p className="text-sm text-destructive">{t("fleet.loadError")}</p> : null}

      <Card className="dashboard-surface border-border/60 shadow-none">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-muted-foreground">{t("fleet.tableTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-left text-sm">
            <thead>
              <tr className="border-b border-border/60 text-xs text-muted-foreground">
                <th className="py-2 pr-4 font-medium">{t("fleet.columns.name")}</th>
                <th className="py-2 pr-4 font-medium">{t("fleet.columns.status")}</th>
                <th className="py-2 pr-4 font-medium">{t("fleet.columns.imageDigest")}</th>
                <th className="py-2 pr-4 font-medium">{t("fleet.columns.health")}</th>
                <th className="py-2 pr-4 font-medium">{t("fleet.columns.image")}</th>
                <th className="py-2 pr-4 font-medium">{t("fleet.columns.revision")}</th>
                <th className="py-2 pr-4 font-medium">{t("fleet.columns.age")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row: FleetRow) => (
                <tr key={row.container_name} className="border-b border-border/40 last:border-0">
                  <td className="py-2 pr-4 font-mono text-xs">
                    <Link
                      to="/ops"
                      search={{ container: row.container_name }}
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      {row.container_name}
                    </Link>
                  </td>
                  <td className="py-2 pr-4">
                    <Badge variant={statusVariant(row.status)}>
                      {t(`fleet.status.${row.status}`)}
                    </Badge>
                  </td>
                  <td className="py-2 pr-4">
                    <Badge variant={digestVariant(row.image_digest_status)}>
                      {t(`fleet.imageDigest.${row.image_digest_status}`)}
                    </Badge>
                  </td>
                  <td className="py-2 pr-4 capitalize text-muted-foreground">{row.health}</td>
                  <td className="py-2 pr-4 max-w-[220px] truncate text-xs text-muted-foreground">
                    {row.image_ref}
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">
                    {row.image_revision ?? "—"}
                  </td>
                  <td className="py-2 pr-4 text-muted-foreground">{formatAge(row.age_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">{t("fleet.empty")}</p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}