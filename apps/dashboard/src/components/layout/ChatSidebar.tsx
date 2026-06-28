import { ArrowCounterClockwise, Plus, X } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { type PopoverOption, PopoverSelect } from "@/components/ui/popover-select";
import { displayAgentName } from "@/lib/agents";
import { type AgentHealth, fetchSessions, resumeSession } from "@/lib/api";
import type { ChatTab } from "@/lib/chats-storage";
import { cn } from "@/lib/utils";

const PLATFORM_LABEL: Record<string, string> = {
  telegram: "TG",
  discord: "DC",
  web: "Web",
};

interface ChatSidebarProps {
  tabs: ChatTab[];
  activeId: string | null;
  agents: string[];
  healthByAgent: Map<string, AgentHealth | undefined>;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onNew: (agent: string) => void;
  onResumed: (agent: string, sessionId: string) => void;
}

export function ChatSidebar({
  tabs,
  activeId,
  agents,
  healthByAgent,
  onSelect,
  onClose,
  onNew,
  onResumed,
}: ChatSidebarProps) {
  const activeTab = tabs.find((t) => t.id === activeId) ?? tabs[0];
  const agent = activeTab?.agent ?? agents[0] ?? null;

  const [pickerAgent, setPickerAgent] = useState(agents[0] ?? "lyra");

  const agentOptions: PopoverOption[] = agents.map((id) => {
    const health = healthByAgent.get(id);
    const online = health?.online !== false;
    return {
      value: id,
      label: displayAgentName(id),
      hint: online ? "En ligne" : "Hors ligne",
      disabled: false,
    };
  });

  const {
    data: sessions = [],
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ["sessions", agent],
    queryFn: () => fetchSessions(agent ?? ""),
    enabled: Boolean(agent),
  });

  const onResume = async (cliId: string | null, sessionId: string) => {
    if (!agent || !cliId) return;
    const res = await resumeSession(agent, cliId);
    if (res.accepted) {
      onResumed(agent, sessionId);
      void refetch();
    }
  };

  return (
    <aside className="flex w-72 shrink-0 flex-col bg-card/60">
      <div className="space-y-2 px-3 py-3">
        <div className="flex items-center gap-2">
          <PopoverSelect
            label="Agent"
            value={pickerAgent}
            options={agentOptions}
            onChange={setPickerAgent}
            className="flex-1"
          />
          <Button
            size="sm"
            className="shrink-0 gap-1"
            onClick={() => onNew(pickerAgent)}
            disabled={!pickerAgent}
          >
            <Plus className="size-4" />
            New
          </Button>
        </div>
      </div>

      <div className="px-3 pb-2">
        <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Chats actifs
        </p>
        <ul className="fd-scroll max-h-44 space-y-0.5 overflow-y-auto">
          {tabs.map((tab) => {
            const active = tab.id === activeId;
            return (
              <li key={tab.id}>
                <div
                  className={cn(
                    "group flex items-center rounded-lg",
                    active ? "bg-primary/10" : "hover:bg-muted/40",
                  )}
                >
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left"
                    onClick={() => onSelect(tab.id)}
                  >
                    <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-brand/15 text-[10px] font-semibold text-brand">
                      {displayAgentName(tab.agent).slice(0, 1)}
                    </span>
                    <span className="truncate text-sm">{displayAgentName(tab.agent)}</span>
                  </button>
                  <button
                    type="button"
                    className="mr-1 rounded p-1 text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100"
                    aria-label="Fermer"
                    onClick={() => onClose(tab.id)}
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="mt-auto flex min-h-0 flex-1 flex-col px-3 pb-3">
        <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Reprendre
        </p>
        {isLoading ? <p className="text-xs text-muted-foreground">Chargement…</p> : null}
        <ul className="fd-scroll space-y-1 overflow-y-auto">
          {sessions.length === 0 && !isLoading ? (
            <li className="rounded-lg bg-muted/30 px-3 py-3 text-center text-xs text-muted-foreground">
              Aucune session
            </li>
          ) : null}
          {sessions.map((s) => (
            <li key={s.session_id}>
              <button
                type="button"
                disabled={!s.cli_session_id}
                className="flex w-full items-start justify-between gap-2 rounded-lg bg-muted/30 px-2.5 py-2 text-left transition-colors hover:bg-muted/60 disabled:opacity-40"
                onClick={() => onResume(s.cli_session_id, s.session_id)}
              >
                <div className="min-w-0">
                  <Badge variant="secondary" className="mb-1 text-[10px]">
                    {PLATFORM_LABEL[s.platform] ?? s.platform}
                  </Badge>
                  <p className="truncate text-xs text-foreground">{s.first_user_msg ?? "(vide)"}</p>
                  <p className="text-[10px] text-muted-foreground">{s.turn_count} tours</p>
                </div>
                <ArrowCounterClockwise className="mt-1 size-3.5 shrink-0 text-muted-foreground" />
              </button>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
