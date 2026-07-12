import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { createLinkCode, fetchLinkStatus, unlinkPlatform } from "@/features/auth/api";
import { ChatReadyBadge } from "@/features/auth/components/chat-ready-badge";
import { PageHeader } from "@/shared/components/page-header";

export function AccountLinksPage() {
  const { t } = useTranslation("auth");
  const qc = useQueryClient();
  const statusQ = useQuery({
    queryKey: ["auth", "links"],
    queryFn: fetchLinkStatus,
  });
  const [lastCode, setLastCode] = useState<{
    platform: string | null;
    token: string;
    instructions: string;
  } | null>(null);

  const mint = useMutation({
    mutationFn: (platform: string | null) => createLinkCode(platform),
    onSuccess: (data, platform) => {
      setLastCode({
        platform: platform,
        token: data.token,
        instructions: data.instructions,
      });
    },
  });

  const unlink = useMutation({
    mutationFn: (platform: string) => unlinkPlatform(platform),
    onSuccess: async () => {
      setLastCode(null);
      await qc.invalidateQueries({ queryKey: ["auth", "links"] });
    },
  });

  const data = statusQ.data;
  const linked = new Set((data?.links ?? []).map((l) => l.platform));

  return (
    <div className="space-y-6">
      <PageHeader title={t("links.title")} description={t("links.subtitle")} />
      <div className="flex flex-wrap items-center gap-3">
        <ChatReadyBadge ready={Boolean(data?.chat_ready)} />
        {statusQ.isLoading ? (
          <span className="text-sm text-muted-foreground">{t("links.loading")}</span>
        ) : null}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {(["telegram", "discord"] as const).map((platform) => {
          const isLinked = linked.has(platform);
          const row = data?.links.find((l) => l.platform === platform);
          return (
            <Card key={platform}>
              <CardHeader>
                <CardTitle className="capitalize">{platform}</CardTitle>
                <CardDescription>
                  {isLinked
                    ? t("links.linkedAs", { key: row?.platform_key })
                    : t("links.notLinked")}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={mint.isPending}
                  onClick={() => mint.mutate(platform)}
                >
                  {t("links.mintCode")}
                </Button>
                {isLinked ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={unlink.isPending}
                    onClick={() => unlink.mutate(platform)}
                  >
                    {t("links.unlink")}
                  </Button>
                ) : null}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {lastCode ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("links.codeTitle")}</CardTitle>
            <CardDescription>{lastCode.instructions}</CardDescription>
          </CardHeader>
          <CardContent>
            <code className="block rounded-lg bg-muted px-3 py-2 font-mono text-sm">
              /link {lastCode.token}
            </code>
            <p className="mt-2 text-xs text-muted-foreground">{t("links.codeHint")}</p>
          </CardContent>
        </Card>
      ) : null}

      {data && !data.chat_ready ? (
        <p className="text-sm text-muted-foreground">{t("links.needBoth")}</p>
      ) : null}

      {data?.chat_ready ? <Badge variant="default">{t("links.readyFull")}</Badge> : null}
    </div>
  );
}
