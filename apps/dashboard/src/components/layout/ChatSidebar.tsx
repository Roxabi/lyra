import { Badge } from "@astryxdesign/core/Badge";
import { ArrowCounterClockwise, Plus, X } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { Button } from "@/components/ui/button";
import { type PopoverOption, PopoverSelect } from "@/components/ui/popover-select";
import { getAgentPersona } from "@/lib/agent-catalog";
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
  const { t } = useTranslation("chat");
  const { t: tc } = useTranslation("common");
  const activeTab = tabs.find((tab) => tab.id === activeId) ?? tabs[0];
  const agent = activeTab?.agent ?? agents[0] ?? null;

  const [pickerAgent, setPickerAgent] = useState(agents[0] ?? "lyra");

  const agentOptions: PopoverOption[] = agents.map((id) => {
    const health = healthByAgent.get(id);
    const online = health?.online !== false;
    return {
      value: id,
      label: displayAgentName(id),
      hint: online ? tc("status.online") : tc("status.offline"),
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
    <aside className="flex h-full min-h-0 w-72 shrink-0 flex-col bg-card/60">
      <div className="shrink-0 space-y-2 px-3 py-3">
        <div className="flex items-center gap-2">
          <PopoverSelect
            label={t("sidebar.agentLabel")}
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
            {t("sidebar.new")}
          </Button>
        </div>
      </div>

      <div className="shrink-0 px-3 pb-2">
        <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("sidebar.activeChats")}
        </p>
        <ul className="space-y-0.5">
          {tabs.map((tab) => {
            const active = tab.id === activeId;
            return (
              <li key={tab.id}>
                <div
                  className={cn(
                    "group relative rounded-lg",
                    active ? "bg-primary/10" : "hover:bg-muted/40",
                  )}
                >
                  <button
                    type="button"
                    className="flex w-full min-w-0 items-center gap-2 px-2 py-2 pr-8 text-left"
                    onClick={() => onSelect(tab.id)}
                  >
                    <AgentAvatar agentId={tab.agent} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {getAgentPersona(tab.agent).displayName}
                      </span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {tab.sessionId
                          ? t("sidebar.sessionHint", { id: tab.sessionId.slice(0, 8) })
                          : t("sidebar.newSession")}
                        {" · "}
                        {tab.model}
                      </span>
                    </span>
                  </button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="absolute right-1 top-1/2 size-7 -translate-y-1/2 opacity-0 group-hover:opacity-100"
                    aria-label={t("sidebar.closeTab")}
                    onClick={() => onClose(tab.id)}
                  >
                    <X className="size-3.5" />
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="flex min-h-0 flex-1 flex-col px-3 pb-3">
        <p className="mb-1.5 shrink-0 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("sidebar.resume")}
        </p>
        {isLoading ? (
          <p className="shrink-0 text-xs text-muted-foreground">{tc("actions.loading")}</p>
        ) : null}
        <ul className="fd-scroll min-h-0 flex-1 space-y-1 overflow-y-auto">
          {sessions.length === 0 && !isLoading ? (
            <li className="rounded-lg bg-muted/30 px-3 py-3 text-center text-xs text-muted-foreground">
              {t("sidebar.noSessions")}
            </li>
          ) : null}
          {sessions.map((s) => (
            <li key={s.session_id}>
              <Button
                type="button"
                variant="ghost"
                disabled={!s.cli_session_id}
                className="h-auto w-full items-start justify-between gap-2 rounded-lg bg-muted/30 px-2.5 py-2 text-left hover:bg-muted/60"
                onClick={() => onResume(s.cli_session_id, s.session_id)}
              >
                <div className="min-w-0">
                  <Badge
                    variant="neutral"
                    className="mb-1 text-[10px]"
                    label={PLATFORM_LABEL[s.platform] ?? s.platform}
                  />
                  <p className="truncate text-xs text-foreground">
                    {s.first_user_msg ?? t("sidebar.emptyMessage")}
                  </p>
                  <p className="text-[10px] text-muted-foreground">
                    {t("sidebar.turnCount", { count: s.turn_count })}
                  </p>
                </div>
                <ArrowCounterClockwise className="mt-1 size-3.5 shrink-0 text-muted-foreground" />
              </Button>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
