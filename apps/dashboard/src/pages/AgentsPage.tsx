import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { HarnessPicker } from "@/components/HarnessPicker";
import { PageIntro } from "@/components/layout/PageIntro";
import { ModelPicker } from "@/components/ModelPicker";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
  const { t } = useTranslation();
  const { data, isLoading } = useQuery({
    queryKey: ["agents-config"],
    queryFn: fetchAgentsConfigList,
  });

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <PageIntro>{t("nav.agents", { defaultValue: "Agents" })}</PageIntro>
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <ul className="space-y-2">
          {(data?.agents ?? []).map((a) => (
            <li key={a.name}>
              <Link
                to="/agents/$name"
                params={{ name: a.name }}
                className="flex items-center justify-between rounded-lg border bg-card px-4 py-3 hover:bg-muted/40"
              >
                <span className="font-medium">{a.name}</span>
                <span className="text-xs text-muted-foreground">
                  {a.backend} · {a.model}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AgentDetailPage() {
  const { name } = useParams({ from: "/agents/$name" });
  const qc = useQueryClient();
  const [tab, setTab] = useState<string>("Identity");
  const [dirty, setDirty] = useState(false);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [sections, setSections] = useState<SoulSections>({});
  const [preview, setPreview] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [tagline, setTagline] = useState("");
  const [harness, setHarness] = useState<"claude-cli" | "omp-rpc">("claude-cli");
  const [model, setModel] = useState("sonnet");

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
      setSaveNotice(
        "Saved. Active chat sessions keep the previous soul until you open a new tab or reset.",
      );
      void qc.invalidateQueries({ queryKey: ["agent-config", name] });
      void qc.invalidateQueries({ queryKey: ["agent-soul", name] });
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

  if (configQ.isLoading) {
    return <p className="p-6 text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <h1 className="font-[family-name:var(--font-head)] text-2xl font-bold">{name}</h1>

      {dirty ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
          Unsaved changes — save before leaving.
        </div>
      ) : null}

      {saveNotice ? (
        <div
          className="rounded-md border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-sm"
          role="status"
        >
          {saveNotice}
        </div>
      ) : null}

      <Card className="space-y-4 p-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1 text-sm">
            <span>Display name</span>
            <Input
              value={displayName}
              onChange={(e) => {
                setDisplayName(e.target.value);
                setDirty(true);
              }}
            />
          </div>
          <div className="space-y-1 text-sm">
            <span>Tagline</span>
            <Input
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
      </Card>

      <Card className="p-4">
        <p className="mb-3 rounded-md border-l-4 border-amber-500 bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-100">
          Session lag: soul edits apply to <strong>new sessions only</strong>. Active chats keep the
          previous soul until you reset or start a new conversation tab.
        </p>
        <div className="mb-3 flex flex-wrap gap-1 border-b pb-2">
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
        <Textarea
          className="min-h-[240px] font-mono text-sm"
          value={sections[tab] ?? ""}
          onChange={(e) => onSectionChange(tab, e.target.value)}
        />
        <p className="mt-2 text-xs text-muted-foreground">
          Document size: {docBytes} / 49152 bytes
        </p>
        {secretWarning ? (
          <p
            className="mt-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            role="alert"
          >
            {secretWarning}
          </p>
        ) : null}
        <div className="mt-4 flex flex-wrap gap-2">
          <Button type="button" variant="secondary" onClick={() => previewMut.mutate()}>
            Preview compose
          </Button>
          <Button
            type="button"
            disabled={saveMut.isPending || docBytes > 49152}
            onClick={() => saveMut.mutate()}
          >
            Save
          </Button>
        </div>
        {preview ? (
          <pre className="mt-4 max-h-48 overflow-auto rounded bg-muted p-3 text-xs">{preview}</pre>
        ) : null}
      </Card>
    </div>
  );
}
