import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useShellTitle } from "@/app/shell-title";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchAgentConfig,
  fetchAgentSoul,
  fetchVoiceCapabilities,
  patchAgentConfig,
  previewAgentSoul,
  putAgentSoul,
  type SoulSections,
} from "@/features/agents/api";
import { AgentIdentity } from "@/shared/components/agent-identity";
import { HarnessPicker } from "@/shared/components/harness-picker";
import { ModelPicker } from "@/shared/components/model-picker";
import { VoicePicker } from "@/shared/components/voice-picker";
import { SOUL_SECTIONS } from "@/shared/lib/agents-constants";
import type { HarnessKind } from "@/shared/lib/chats-storage";
import { formatSoulSecretWarning, scanSoulMarkdownForSecrets } from "@/shared/lib/soul-secret-lint";

function readTtsField(
  voiceJson: Record<string, unknown> | null | undefined,
  key: string,
): string {
  const tts = voiceJson?.tts;
  if (!tts || typeof tts !== "object") return "";
  const val = (tts as Record<string, unknown>)[key];
  return typeof val === "string" ? val : "";
}

function composeSoulMarkdown(sections: SoulSections): string {
  return SOUL_SECTIONS.map((s) => {
    const body = sections[s]?.trim() ?? "";
    return body ? `## ${s}\n${body}` : "";
  })
    .filter(Boolean)
    .join("\n\n");
}

export function AgentDetailPage() {
  const { name } = useParams({ from: "/agents/$name" });
  const { t } = useTranslation("agents");
  const { setLiteral } = useShellTitle();
  const qc = useQueryClient();
  const [tab, setTab] = useState<string>("Identity");
  const [dirty, setDirty] = useState(false);

  const [sections, setSections] = useState<SoulSections>({});
  const [preview, setPreview] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [tagline, setTagline] = useState("");
  const [harness, setHarness] = useState<HarnessKind>("claude-cli");
  const [model, setModel] = useState("sonnet");
  const [ttsEngine, setTtsEngine] = useState("xai");
  const [ttsVoice, setTtsVoice] = useState("eve");

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

  const voiceCapsQ = useQuery({
    queryKey: ["voice-capabilities"],
    queryFn: fetchVoiceCapabilities,
    staleTime: 60_000,
  });

  useEffect(() => {
    const cfg = configQ.data;
    if (!cfg) return;
    setHarness(cfg.backend);
    setModel(cfg.model);
    setDisplayName(cfg.soul_meta_json?.header?.display_name ?? "");
    setTagline(cfg.soul_meta_json?.header?.tagline ?? "");
    const eng = readTtsField(cfg.voice_json, "engine");
    const voi = readTtsField(cfg.voice_json, "voice");
    setTtsEngine(eng || voiceCapsQ.data?.tts?.default_engine || "xai");
    setTtsVoice(voi || "eve");
  }, [configQ.data, voiceCapsQ.data?.tts?.default_engine]);

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
      const current = configQ.data;
      const prevTts =
        current?.voice_json?.tts && typeof current.voice_json.tts === "object"
          ? (current.voice_json.tts as Record<string, unknown>)
          : {};
      const prevStt =
        current?.voice_json?.stt && typeof current.voice_json.stt === "object"
          ? (current.voice_json.stt as Record<string, unknown>)
          : {};
      await patchAgentConfig(name, {
        backend: harness,
        model,
        display_name: displayName,
        tagline,
        voice_json: {
          tts: {
            ...prevTts,
            engine: ttsEngine || null,
            voice: ttsVoice || null,
          },
          stt: prevStt,
        },
      });
      await putAgentSoul(name, { markdown: md });
    },
    onSuccess: () => {
      setDirty(false);
      toast.info(t("saveNotice"));
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
    return (
      <Alert variant="destructive">
        <AlertTitle>{t("loadError")}</AlertTitle>
      </Alert>
    );
  }

  return (
    <div className="space-y-6 pb-8">
      <div className="space-y-1">
        <AgentIdentity
          agentId={name}
          avatarSize="lg"
          nameClassName="font-heading text-lg font-semibold"
        />
        <p className="text-sm text-muted-foreground">{t("detailSubtitle", { name })}</p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 pt-6 text-sm">
          <span className="font-medium text-muted-foreground">{t("defaultsTitle")}</span>
          <Badge variant="outline" className="font-mono">
            {cfg?.backend ?? harness}
          </Badge>
          <Badge variant="outline" className="font-mono">
            {cfg?.model ?? model}
          </Badge>
          <Badge variant={hasSoulBlob ? "default" : "secondary"}>
            {t("soulSource", { source: t(soulSourceKey) })}
          </Badge>
          {cfg?.updated_at ? (
            <span className="text-xs text-muted-foreground">
              {t("updatedAt", { date: cfg.updated_at })}
            </span>
          ) : null}
        </CardContent>
      </Card>

      {soulQ.isError ? (
        <Alert variant="destructive">
          <AlertTitle>{t("soulLoadError")}</AlertTitle>
        </Alert>
      ) : null}

      {!soulQ.isError && !hasSectionContent ? (
        <Alert>
          <AlertTitle>{t("noSoulContentTitle")}</AlertTitle>
          <AlertDescription>{t("noSoulContent")}</AlertDescription>
        </Alert>
      ) : null}

      {dirty ? (
        <Alert>
          <AlertTitle>{t("unsavedChanges")}</AlertTitle>
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">{t("defaultsTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="display-name">{t("displayName")}</Label>
              <Input
                id="display-name"
                value={displayName}
                onChange={(e) => {
                  setDisplayName(e.target.value);
                  setDirty(true);
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="tagline">{t("tagline")}</Label>
              <Input
                id="tagline"
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

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">{t("voiceTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">{t("voiceHint")}</p>
          <VoicePicker
            engines={voiceCapsQ.data?.tts?.engines ?? []}
            voices={voiceCapsQ.data?.tts?.voices ?? []}
            engine={ttsEngine}
            voice={ttsVoice}
            offline={voiceCapsQ.isLoading}
            error={voiceCapsQ.isError || Boolean(voiceCapsQ.data?.error)}
            onEngineChange={(v) => {
              setTtsEngine(v);
              setDirty(true);
            }}
            onVoiceChange={(v) => {
              setTtsVoice(v);
              setDirty(true);
            }}
          />
          {ttsVoice ? (
            <Badge variant="outline" className="font-mono">
              {ttsEngine || "xai"} / {ttsVoice}
            </Badge>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Soul</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Alert>
            <AlertTitle>{t("sessionLagTitle")}</AlertTitle>
            <AlertDescription>
              {t("sessionLagPrefix")} <strong>{t("sessionLagStrong")}</strong>
              {t("sessionLagSuffix")}
            </AlertDescription>
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

          <div className="space-y-2">
            <Label htmlFor="soul-section">{t("soulSectionEditor", { section: tab })}</Label>
            <Textarea
              id="soul-section"
              className="min-h-48 font-mono text-xs"
              value={sections[tab] ?? ""}
              onChange={(e) => onSectionChange(tab, e.target.value)}
              rows={12}
            />
          </div>

          <p className="text-xs text-muted-foreground">{t("documentSize", { bytes: docBytes })}</p>

          {secretWarning ? (
            <Alert variant="destructive">
              <AlertDescription>{secretWarning}</AlertDescription>
            </Alert>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" onClick={() => previewMut.mutate()}>
              {t("previewCompose")}
            </Button>
            <Button
              type="button"
              disabled={saveMut.isPending || docBytes > 49152}
              onClick={() => saveMut.mutate()}
            >
              {saveMut.isPending ? <Spinner className="mr-1.5" /> : null}
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
