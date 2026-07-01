import { TextArea } from "@astryxdesign/core/TextArea";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentIdentity } from "@/components/agents/AgentIdentity";
import { AgentsListPanel } from "@/components/agents/AgentsListPanel";
import { HarnessPicker } from "@/components/HarnessPicker";
import { PageIntro } from "@/components/layout/PageIntro";
import { ModelPicker } from "@/components/ModelPicker";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/sonner";
import {
  fetchAgentConfig,
  fetchAgentSoul,
  fetchAgentsConfigList,
  patchAgentConfig,
  previewAgentSoul,
  putAgentSoul,
  type SoulSections,
} from "@/lib/agents-api";
import { SOUL_SECTIONS } from "@/lib/agents-constants";
import { useShellTitleContext } from "@/lib/shell-title";
import { formatSoulSecretWarning, scanSoulMarkdownForSecrets } from "@/lib/soul-secret-lint";

function composeSoulMarkdown(sections: SoulSections): string {
  return SOUL_SECTIONS.map((s) => {
    const body = sections[s]?.trim() ?? "";
    return body ? `## ${s}\n${body}` : "";
  })
    .filter(Boolean)
    .join("\n\n");
}

export function AgentsListPage() {
  const { t } = useTranslation("agents");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["agents-config"],
    queryFn: fetchAgentsConfigList,
  });

  return (
    <div className="space-y-6 pb-8">
      <PageIntro>{t("subtitle")}</PageIntro>
      <AgentsListPanel agents={data?.agents ?? []} isLoading={isLoading} isError={isError} />
    </div>
  );
}

export function AgentDetailPage() {
  const { name } = useParams({ from: "/agents/$name" });
  const { t } = useTranslation("agents");
  const { setLiteral } = useShellTitleContext();
  const qc = useQueryClient();
  const [tab, setTab] = useState<string>("Identity");
  const [dirty, setDirty] = useState(false);

  const [sections, setSections] = useState<SoulSections>({});
  const [preview, setPreview] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [tagline, setTagline] = useState("");
  const [harness, setHarness] = useState<"claude-cli" | "omp-rpc">("claude-cli");
  const [model, setModel] = useState("sonnet");

  useEffect(() => {
    setLiteral(name);
    return () => setLiteral(null);
  }, [name, setLiteral]);

  const configQ = useQuery({
    queryKey: ["agent-config", name],
    queryFn: () => fetchAgentConfig(name),
  });

  const soulQ = useQuery({
    queryKey: ["agent-soul", name],
    queryFn: () => fetchAgentSoul(name),
    enabled: !!name,
  });

  useEffect(() => {
    const cfg = configQ.data;
    if (!cfg) return;
    setHarness(cfg.backend);
    setModel(cfg.model);
    setDisplayName(cfg.soul_meta_json?.header?.display_name ?? "");
    setTagline(cfg.soul_meta_json?.header?.tagline ?? "");
  }, [configQ.data]);

  useEffect(() => {
    if (soulQ.data?.sections) {
      setSections(soulQ.data.sections);
    }
  }, [soulQ.data]);

  const soulMarkdown = useMemo(() => composeSoulMarkdown(sections), [sections]);

  const docBytes = useMemo(() => new TextEncoder().encode(soulMarkdown).length, [soulMarkdown]);

  const secretWarning = useMemo(
    () => formatSoulSecretWarning(scanSoulMarkdownForSecrets(soulMarkdown)),
    [soulMarkdown],
  );

  const saveMut = useMutation({
    mutationFn: async () => {
      const md = soulMarkdown;
      await patchAgentConfig(name, {
        backend: harness,
        model,
        display_name: displayName,
        tagline,
      });
      await putAgentSoul(name, { markdown: md });
    },
    onSuccess: () => {
      setDirty(false);
      toast.success(t("saveNotice"));
      void qc.invalidateQueries({ queryKey: ["agent-config", name] });
      void qc.invalidateQueries({ queryKey: ["agent-soul", name] });
      void qc.invalidateQueries({ queryKey: ["agents-config"] });
    },
    onError: () => {
      toast.error(t("saveError"));
    },
  });

  const previewMut = useMutation({
    mutationFn: () => previewAgentSoul(name, { sections }),
    onSuccess: (res) => setPreview(res.composed),
  });

  const onSectionChange = useCallback((key: string, value: string) => {
    setSections((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  }, []);

  const cfg = configQ.data;
  const soulSections = soulQ.data?.sections ?? {};
  const hasSoulBlob = Boolean(cfg?.soul_document_blob_ref);
  const hasSectionContent = SOUL_SECTIONS.some((s) => (soulSections[s] ?? "").trim().length > 0);
  const soulSourceKey = hasSoulBlob
    ? "soulSourceBlob"
    : hasSectionContent
      ? "soulSourceLegacy"
      : "soulSourceEmpty";

  if (configQ.isLoading || soulQ.isLoading) {
    return <p className="text-sm text-muted-foreground">{t("loadingConfig")}</p>;
  }

  if (configQ.isError) {
    return <Alert variant="destructive">{t("loadError")}</Alert>;
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <AgentIdentity
          agentId={name}
          avatarSize="lg"
          nameClassName="font-[family-name:var(--font-head)] text-lg font-semibold"
        />
        <p className="text-sm text-muted-foreground">{t("detailSubtitle", { name })}</p>
      </div>

      <Card className="dashboard-surface border-border/60 shadow-none">
        <CardContent className="flex flex-wrap items-center gap-3 p-4 text-sm">
          <span className="font-medium text-muted-foreground">{t("defaultsTitle")}</span>
          <Badge variant="secondary" className="font-mono">
            {cfg?.backend ?? harness}
          </Badge>
          <Badge variant="secondary" className="font-mono">
            {cfg?.model ?? model}
          </Badge>
          <Badge variant={hasSoulBlob ? "success" : "warning"}>
            {t("soulSource", { source: t(soulSourceKey) })}
          </Badge>
          {cfg?.updated_at ? (
            <span className="text-xs text-muted-foreground">
              {t("updatedAt", { date: cfg.updated_at })}
            </span>
          ) : null}
        </CardContent>
      </Card>

      {soulQ.isError ? <Alert variant="destructive">{t("soulLoadError")}</Alert> : null}

      {!soulQ.isError && !hasSectionContent ? (
        <Alert variant="warning">{t("noSoulContent")}</Alert>
      ) : null}

      {dirty ? <Alert variant="warning">{t("unsavedChanges")}</Alert> : null}

      <Card className="dashboard-surface border-border/60 shadow-none">
        <CardContent className="space-y-4 p-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <label
                className="text-xs font-medium text-muted-foreground"
                htmlFor="agent-display-name"
              >
                {t("displayName")}
              </label>
              <Input
                id="agent-display-name"
                value={displayName}
                onChange={(e) => {
                  setDisplayName(e.target.value);
                  setDirty(true);
                }}
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground" htmlFor="agent-tagline">
                {t("tagline")}
              </label>
              <Input
                id="agent-tagline"
                value={tagline}
                onChange={(e) => {
                  setTagline(e.target.value);
                  setDirty(true);
                }}
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-4">
            <HarnessPicker
              value={harness}
              onChange={(v) => {
                setHarness(v);
                setDirty(true);
              }}
            />
            <ModelPicker
              harness={harness}
              value={model}
              onChange={(v) => {
                setModel(v);
                setDirty(true);
              }}
            />
          </div>
        </CardContent>
      </Card>

      <Card className="dashboard-surface border-border/60 shadow-none">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">Soul</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Alert variant="warning">
            {t("sessionLagPrefix")} <strong>{t("sessionLagStrong")}</strong>
            {t("sessionLagSuffix")}
          </Alert>
          <div className="flex flex-wrap gap-1 border-b border-border/40 pb-2">
            {SOUL_SECTIONS.map((s) => (
              <Button
                key={s}
                type="button"
                size="sm"
                variant={tab === s ? "default" : "ghost"}
                onClick={() => setTab(s)}
              >
                {s}
              </Button>
            ))}
          </div>
          <TextArea
            label={t("soulSectionEditor", { section: tab })}
            isLabelHidden
            size="sm"
            data-mono
            value={sections[tab] ?? ""}
            onChange={(next) => onSectionChange(tab, next)}
            rows={12}
          />
          <p className="text-xs text-muted-foreground">{t("documentSize", { bytes: docBytes })}</p>
          {secretWarning ? <Alert variant="destructive">{secretWarning}</Alert> : null}
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" onClick={() => previewMut.mutate()}>
              {t("previewCompose")}
            </Button>
            <Button
              type="button"
              disabled={saveMut.isPending || docBytes > 49152}
              loading={saveMut.isPending}
              onClick={() => saveMut.mutate()}
            >
              {t("save")}
            </Button>
          </div>
          {preview ? (
            <pre className="max-h-48 overflow-auto rounded-md bg-muted p-3 text-xs">{preview}</pre>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
