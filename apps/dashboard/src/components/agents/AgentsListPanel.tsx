import { Skeleton } from "@astryxdesign/core/Skeleton";
import { CaretRight, PencilSimple, Plus, Robot, SquaresFour, Table } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentIdentity } from "@/components/agents/AgentIdentity";
import { CreateAgentDialog } from "@/components/agents/CreateAgentDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import {
  ListToolbar,
  ListToolbarControls,
  ListToolbarHeader,
  ListToolbarSearch,
} from "@/components/ui/list-toolbar";
import { PresenceBadge } from "@/components/ui/presence-badge";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { getAgentPersona } from "@/lib/agent-catalog";
import type { AgentSummary } from "@/lib/agents-api";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useCardsTableViewPreference } from "@/lib/use-view-preference";
import { cn } from "@/lib/utils";

function harnessLabel(backend: AgentSummary["backend"]): string {
  return backend === "claude-cli" ? "Clipool" : "OMP";
}

function formatUpdated(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "short",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

function formatSoulSize(
  bytes: number | null,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  if (bytes == null || bytes === 0) return "—";
  if (bytes < 1024) return t("soulSizeBytes", { bytes });
  return t("soulSizeKb", { size: (bytes / 1024).toFixed(1) });
}

function filterAgents(agents: AgentSummary[], query: string): AgentSummary[] {
  const q = query.trim().toLowerCase();
  if (!q) return agents;
  return agents.filter((a) => {
    const persona = getAgentPersona(a.name);
    const haystack = [
      a.name,
      persona.displayName,
      persona.tagline,
      a.model,
      harnessLabel(a.backend),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });
}

function EditAffordance({ className }: { className?: string }) {
  const { t } = useTranslation("agents");
  return (
    <span
      className={cn(
        "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-xs font-medium text-foreground shadow-none",
        className,
      )}
    >
      <PencilSimple className="size-3.5" aria-hidden />
      {t("edit")}
      <CaretRight className="size-3.5 opacity-60" aria-hidden />
    </span>
  );
}

function EditButton({
  agentName,
  onClick,
}: {
  agentName: string;
  onClick?: (e: React.MouseEvent) => void;
}) {
  const { t } = useTranslation("agents");
  return (
    <Button
      variant="outline"
      size="sm"
      className="h-7 shrink-0 gap-1.5 px-2.5 text-xs active:scale-[0.98]"
      asChild
    >
      <Link
        to="/agents/$name"
        params={{ name: agentName }}
        aria-label={t("edit")}
        onClick={onClick}
      >
        <PencilSimple className="size-3.5" aria-hidden />
        {t("edit")}
        <CaretRight className="size-3.5 opacity-60" aria-hidden />
      </Link>
    </Button>
  );
}

function AgentsListSkeleton({ view }: { view: "cards" | "table" }) {
  const { t } = useTranslation("common");

  if (view === "table") {
    return (
      <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
        <div
          role="status"
          className="divide-y divide-border/30 px-4"
          aria-busy="true"
          aria-label={t("actions.loading")}
        >
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex items-center gap-4 py-3">
              <Skeleton width={32} height={32} radius="rounded" className="shrink-0" />
              <Skeleton width={96} height={16} />
              <Skeleton width={56} height={24} radius="rounded" />
              <Skeleton width={80} height={16} />
              <Skeleton width={48} height={24} radius="rounded" />
              <Skeleton width={48} height={24} radius="rounded" />
              <Skeleton width={48} height={24} radius="rounded" />
              <Skeleton width={64} height={28} className="ml-auto" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <ul
      role="status"
      className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"
      aria-busy="true"
      aria-label={t("actions.loading")}
    >
      {[0, 1, 2].map((i) => (
        <li key={i}>
          <div className="rounded-xl border border-border/50 bg-card p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 flex-1 items-center gap-2.5">
                <Skeleton width={36} height={36} radius="rounded" className="shrink-0" />
                <div className="min-w-0 flex-1 space-y-2">
                  <Skeleton width="40%" height={16} />
                  <Skeleton width="60%" height={12} />
                </div>
              </div>
              <Skeleton width={56} height={24} radius="rounded" className="shrink-0" />
            </div>
            <div className="mt-3 flex gap-2">
              <Skeleton width={56} height={20} radius="rounded" />
              <Skeleton width={64} height={20} radius="rounded" />
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

interface AgentsListPanelProps {
  agents: AgentSummary[];
  isLoading?: boolean;
  isError?: boolean;
}

export function AgentsListPanel({ agents, isLoading, isError }: AgentsListPanelProps) {
  const { t } = useTranslation("agents");
  const { t: tc } = useTranslation("common");
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [view, setView] = useCardsTableViewPreference("agents");
  const debouncedSearch = useDebouncedValue(search, 300);

  const filtered = useMemo(() => filterAgents(agents, debouncedSearch), [agents, debouncedSearch]);
  const hasFilters = search.trim().length > 0;

  const viewOptions = [
    { value: "cards" as const, label: t("viewCards"), icon: SquaresFour },
    { value: "table" as const, label: t("viewTable"), icon: Table },
  ];

  return (
    <div className="flex flex-col gap-4 p-4">
      <CreateAgentDialog open={createOpen} onOpenChange={setCreateOpen} />

      <ListToolbar>
        <ListToolbarHeader
          meta={isLoading ? tc("actions.loading") : t("count", { count: filtered.length })}
          actions={
            <Button
              type="button"
              size="sm"
              className="h-8 gap-1.5"
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="size-3.5" aria-hidden />
              {t("createAgent")}
            </Button>
          }
        />
        <ListToolbarSearch
          value={search}
          onChange={setSearch}
          placeholder={t("searchPlaceholder")}
          aria-label={tc("search")}
        />
        <ListToolbarControls
          view={
            <SegmentedControl
              options={viewOptions}
              value={view}
              onChange={setView}
              ariaLabel={t("viewMode")}
              compact="responsive"
            />
          }
        />
      </ListToolbar>

      {isLoading ? <AgentsListSkeleton view={view} /> : null}

      {isError ? (
        <p className="text-sm text-destructive" role="alert">
          {t("listLoadError")}
        </p>
      ) : null}

      {!isLoading && !isError && filtered.length === 0 ? (
        <EmptyState
          icon={hasFilters ? undefined : Robot}
          title={hasFilters ? t("emptyFiltered") : t("empty")}
          hint={hasFilters ? undefined : t("emptyHint")}
          action={
            hasFilters ? undefined : (
              <Button
                type="button"
                size="sm"
                className="gap-1.5"
                onClick={() => setCreateOpen(true)}
              >
                <Plus className="size-3.5" aria-hidden />
                {t("createAgent")}
              </Button>
            )
          }
        />
      ) : null}

      {!isLoading && !isError && filtered.length > 0 && view === "cards" ? (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((a) => {
            const persona = getAgentPersona(a.name);
            return (
              <li key={a.name}>
                <Link
                  to="/agents/$name"
                  params={{ name: a.name }}
                  className="group flex h-full cursor-pointer flex-col rounded-xl border border-border/50 bg-card shadow-sm transition-[border-color,transform,box-shadow] hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="flex items-start justify-between gap-3 px-4 pb-2 pt-4">
                    <AgentIdentity agentId={a.name} subtitle={persona.tagline} />
                    <Badge variant={a.has_soul ? "success" : "warning"} className="shrink-0">
                      {a.has_soul ? t("hasSoul") : t("noSoul")}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 px-4 pb-3">
                    <Badge variant="secondary" className="font-mono text-[10px]">
                      {harnessLabel(a.backend)}
                    </Badge>
                    <Badge variant="secondary" className="font-mono text-[10px]">
                      {a.model}
                    </Badge>
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                      {t("colTelegram")}
                      <PresenceBadge present={a.has_telegram} namespace="agents" />
                    </span>
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                      {t("colDiscord")}
                      <PresenceBadge present={a.has_discord} namespace="agents" />
                    </span>
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                      {t("colEmail")}
                      <PresenceBadge present={a.has_email} namespace="agents" />
                    </span>
                    {a.soul_document_bytes ? (
                      <span className="text-[10px] tabular-nums text-muted-foreground">
                        {formatSoulSize(a.soul_document_bytes, t)}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-auto flex items-center justify-between gap-2 border-t border-border/40 px-4 py-2.5">
                    <span className="text-[10px] tabular-nums text-muted-foreground">
                      {formatUpdated(a.updated_at)}
                    </span>
                    <EditAffordance className="opacity-90 transition-opacity group-hover:border-primary/35 group-hover:opacity-100" />
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      ) : null}

      {!isLoading && !isError && filtered.length > 0 && view === "table" ? (
        <div className="overflow-x-auto rounded-xl border border-border bg-card shadow-sm">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead>
              <tr className="border-b border-border/50 text-xs text-muted-foreground">
                <th scope="col" className="px-4 py-2 pr-4 font-medium">
                  {t("colAgent")}
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  {t("colTagline")}
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  {t("colHarness")}
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  {t("colModel")}
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  {t("colTelegram")}
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  {t("colDiscord")}
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  {t("colEmail")}
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  {t("colSoul")}
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  {t("colSoulSize")}
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  {t("colUpdated")}
                </th>
                <th scope="col" className="py-2 pr-4 pl-2 text-right font-medium">
                  {t("colAction")}
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((a) => {
                const persona = getAgentPersona(a.name);
                return (
                  <tr
                    key={a.name}
                    className="group border-b border-border/30 transition-colors last:border-0 hover:bg-muted/20"
                  >
                    <td className="px-4 py-3 pr-4">
                      <Link
                        to="/agents/$name"
                        params={{ name: a.name }}
                        className="block rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <AgentIdentity agentId={a.name} avatarSize="sm" />
                      </Link>
                    </td>
                    <td className="max-w-[140px] py-3 pr-4 text-xs text-muted-foreground">
                      <span className="line-clamp-2">{persona.tagline}</span>
                    </td>
                    <td className="py-3 pr-4">
                      <Badge variant="secondary" className="font-mono text-[10px]">
                        {harnessLabel(a.backend)}
                      </Badge>
                    </td>
                    <td className="py-3 pr-4 font-mono text-xs text-muted-foreground">{a.model}</td>
                    <td className="py-3 pr-4">
                      <PresenceBadge present={a.has_telegram} namespace="agents" />
                    </td>
                    <td className="py-3 pr-4">
                      <PresenceBadge present={a.has_discord} namespace="agents" />
                    </td>
                    <td className="py-3 pr-4">
                      <PresenceBadge present={a.has_email} namespace="agents" />
                    </td>
                    <td className="py-3 pr-4">
                      <Badge variant={a.has_soul ? "success" : "warning"}>
                        {a.has_soul ? t("hasSoul") : t("noSoul")}
                      </Badge>
                    </td>
                    <td className="py-3 pr-4 text-xs text-muted-foreground tabular-nums">
                      {formatSoulSize(a.soul_document_bytes, t)}
                    </td>
                    <td className="py-3 pr-4 text-xs text-muted-foreground tabular-nums">
                      {formatUpdated(a.updated_at)}
                    </td>
                    <td className="py-3 pr-4 pl-2 text-right">
                      <EditButton agentName={a.name} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
