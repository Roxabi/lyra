import { Link } from "@tanstack/react-router";
import { ChevronRight, LayoutGrid, Pencil, Plus, Table as TableIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { AgentSummary } from "@/features/agents/api";
import { CreateAgentDialog } from "@/features/agents/components/create-agent-dialog";
import { cn } from "@/lib/utils";
import { AgentIdentity } from "@/shared/components/agent-identity";
import { EmptyState } from "@/shared/components/empty-state";
import {
  ListToolbar,
  ListToolbarControls,
  ListToolbarHeader,
  ListToolbarSearch,
} from "@/shared/components/list-toolbar";
import { PresenceBadge } from "@/shared/components/presence-badge";
import { useDebouncedValue } from "@/shared/hooks/use-debounced-value";
import { getAgentPersona } from "@/shared/lib/agent-catalog";

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

function formatSoulSize(bytes: number | null): string {
  if (bytes == null || bytes === 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
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

function AgentsListSkeleton({ view }: { view: "cards" | "table" }) {
  if (view === "table") {
    return (
      <div className="overflow-x-auto rounded-xl border bg-card shadow-sm">
        <div role="status" className="divide-y px-4" aria-busy="true" aria-label="Loading">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex items-center gap-4 py-3">
              <Skeleton className="size-8 shrink-0 rounded-full" />
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-6 w-14 rounded-full" />
              <Skeleton className="ml-auto h-7 w-16" />
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
      aria-label="Loading"
    >
      {[0, 1, 2].map((i) => (
        <li key={i}>
          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 flex-1 items-center gap-2.5">
                <Skeleton className="size-9 shrink-0 rounded-full" />
                <div className="min-w-0 flex-1 space-y-2">
                  <Skeleton className="h-4 w-2/5" />
                  <Skeleton className="h-3 w-3/5" />
                </div>
              </div>
              <Skeleton className="h-6 w-14 rounded-full" />
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function EditAffordance({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border bg-background px-2.5 text-xs font-medium shadow-none",
        className,
      )}
    >
      <Pencil className="size-3.5" aria-hidden />
      Edit
      <ChevronRight className="size-3.5 opacity-60" aria-hidden />
    </span>
  );
}

interface AgentsListPanelProps {
  agents: AgentSummary[];
  isLoading?: boolean;
  isError?: boolean;
}

export function AgentsListPanel({ agents, isLoading, isError }: AgentsListPanelProps) {
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [view, setView] = useState<"cards" | "table">("cards");
  const debouncedSearch = useDebouncedValue(search, 300);

  const filtered = useMemo(() => filterAgents(agents, debouncedSearch), [agents, debouncedSearch]);
  const hasFilters = search.trim().length > 0;

  return (
    <div className="flex flex-col gap-4 p-4">
      <CreateAgentDialog open={createOpen} onOpenChange={setCreateOpen} />

      <ListToolbar>
        <ListToolbarHeader
          meta={
            isLoading ? "Loading…" : `${filtered.length} agent${filtered.length === 1 ? "" : "s"}`
          }
          actions={
            <Button
              type="button"
              size="sm"
              className="h-8 gap-1.5"
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="size-3.5" aria-hidden />
              Create agent
            </Button>
          }
        />
        <ListToolbarSearch
          value={search}
          onChange={setSearch}
          placeholder="Search agents…"
          aria-label="Search"
        />
        <ListToolbarControls
          view={
            <ToggleGroup
              value={[view]}
              onValueChange={(v) => {
                const next = v[0];
                if (next === "cards" || next === "table") setView(next);
              }}
              variant="outline"
              size="sm"
            >
              <ToggleGroupItem value="cards" aria-label="Cards view">
                <LayoutGrid className="size-3.5" />
                Cards
              </ToggleGroupItem>
              <ToggleGroupItem value="table" aria-label="Table view">
                <TableIcon className="size-3.5" />
                Table
              </ToggleGroupItem>
            </ToggleGroup>
          }
        />
      </ListToolbar>

      {isLoading ? <AgentsListSkeleton view={view} /> : null}

      {isError ? (
        <p className="text-sm text-destructive" role="alert">
          Failed to load agents.
        </p>
      ) : null}

      {!isLoading && !isError && filtered.length === 0 ? (
        <EmptyState
          title={hasFilters ? "No matching agents" : "No agents yet"}
          description={hasFilters ? undefined : "Create your first agent to get started."}
          action={
            hasFilters ? undefined : (
              <Button
                type="button"
                size="sm"
                className="gap-1.5"
                onClick={() => setCreateOpen(true)}
              >
                <Plus className="size-3.5" aria-hidden />
                Create agent
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
                  className="group flex h-full cursor-pointer flex-col rounded-xl border bg-card shadow-sm transition-[border-color,transform,box-shadow] hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="flex items-start justify-between gap-3 px-4 pt-4 pb-2">
                    <AgentIdentity agentId={a.name} subtitle={persona.tagline} />
                    <Badge variant={a.has_soul ? "default" : "secondary"} className="shrink-0">
                      {a.has_soul ? "Soul" : "No soul"}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 px-4 pb-3">
                    <Badge variant="outline" className="font-mono text-[10px]">
                      {harnessLabel(a.backend)}
                    </Badge>
                    <Badge variant="outline" className="font-mono text-[10px]">
                      {a.model}
                    </Badge>
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                      TG
                      <PresenceBadge platform="telegram" present={a.has_telegram} />
                    </span>
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                      DC
                      <PresenceBadge platform="discord" present={a.has_discord} />
                    </span>
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                      Email
                      <PresenceBadge platform="email" present={a.has_email} />
                    </span>
                    {a.soul_document_bytes ? (
                      <span className="text-[10px] tabular-nums text-muted-foreground">
                        {formatSoulSize(a.soul_document_bytes)}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-auto flex items-center justify-between gap-2 border-t px-4 py-2.5">
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
        <div className="overflow-x-auto rounded-xl border bg-card shadow-sm">
          <Table className="min-w-[980px]">
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead>Tagline</TableHead>
                <TableHead>Harness</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>TG</TableHead>
                <TableHead>DC</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Soul</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((a) => {
                const persona = getAgentPersona(a.name);
                return (
                  <TableRow key={a.name}>
                    <TableCell>
                      <Link
                        to="/agents/$name"
                        params={{ name: a.name }}
                        className="block rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <AgentIdentity agentId={a.name} avatarSize="sm" />
                      </Link>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      <div className="min-w-0 max-w-[140px]">
                        <span className="line-clamp-2">{persona.tagline}</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {harnessLabel(a.backend)}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {a.model}
                    </TableCell>
                    <TableCell>
                      <PresenceBadge platform="telegram" present={a.has_telegram} />
                    </TableCell>
                    <TableCell>
                      <PresenceBadge platform="discord" present={a.has_discord} />
                    </TableCell>
                    <TableCell>
                      <PresenceBadge platform="email" present={a.has_email} />
                    </TableCell>
                    <TableCell>
                      <Badge variant={a.has_soul ? "default" : "secondary"}>
                        {a.has_soul ? "Soul" : "No soul"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground tabular-nums">
                      {formatSoulSize(a.soul_document_bytes)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground tabular-nums">
                      {formatUpdated(a.updated_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 gap-1.5 px-2.5 text-xs"
                        render={<Link to="/agents/$name" params={{ name: a.name }} />}
                      >
                        <Pencil className="size-3.5" aria-hidden />
                        Edit
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </div>
  );
}
