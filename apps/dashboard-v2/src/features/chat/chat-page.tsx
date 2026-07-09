import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { fetchAgentDefaults } from "@/features/agents/api";
import { fetchAgents, fetchSessionTurns } from "@/features/chat/api";
import { ChatPane } from "@/features/chat/components/chat-pane";
import { ChatSidebar } from "@/features/chat/components/chat-sidebar";
import { CockpitContextPanel } from "@/features/chat/components/cockpit-context-panel";
import { useAgentStatus } from "@/shared/hooks/use-agent-status";
import { turnsToLog } from "@/shared/lib/chat-messages";
import {
  type AgentDefaults,
  type ChatTab,
  loadTabs,
  newTab,
  saveTabs,
} from "@/shared/lib/chats-storage";

export function ChatPage() {
  const [tabs, setTabs] = useState<ChatTab[]>(() => loadTabs());
  const [activeId, setActiveId] = useState<string | null>(() => loadTabs()[0]?.id ?? null);
  const [hydratedLog, setHydratedLog] = useState<string | null>(null);
  const [defaultsWarning, setDefaultsWarning] = useState<string | null>(null);

  const {
    data: agents = [],
    isLoading: agentsLoading,
    isError: agentsError,
  } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const activeTab = useMemo(() => tabs.find((t) => t.id === activeId) ?? tabs[0], [tabs, activeId]);
  const { data: dbDefaults } = useQuery({
    queryKey: ["agent-defaults", activeTab?.agent],
    queryFn: () => fetchAgentDefaults(activeTab?.agent ?? ""),
    enabled: !!activeTab?.agent,
  });
  const { healthFor, status } = useAgentStatus(activeTab);

  const healthByAgent = useMemo(() => {
    const map = new Map<string, (typeof status)[number]>();
    for (const row of status) map.set(row.agent, row);
    return map;
  }, [status]);

  useEffect(() => {
    saveTabs(tabs);
  }, [tabs]);

  useEffect(() => {
    if (tabs.length === 0 && agents.length > 0) {
      void (async () => {
        let defaults: AgentDefaults | undefined;
        try {
          defaults = await fetchAgentDefaults(agents[0]);
        } catch {
          defaults = undefined;
          setDefaultsWarning(
            `Could not load DB defaults for ${agents[0]}; using fallback harness/model.`,
          );
        }
        const t = newTab(agents[0], defaults);
        setTabs([t]);
        setActiveId(t.id);
      })();
    }
  }, [agents, tabs.length]);

  const updateTab = (id: string, patch: Partial<ChatTab>) => {
    setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  };

  const addTab = (agent: string) => {
    void (async () => {
      let defaults: AgentDefaults | undefined;
      try {
        defaults = await fetchAgentDefaults(agent);
      } catch {
        defaults = undefined;
        setDefaultsWarning(
          `Could not load DB defaults for ${agent}; using fallback harness/model.`,
        );
      }
      const t = newTab(agent, defaults);
      setTabs((prev) => [...prev, t]);
      setActiveId(t.id);
      setHydratedLog(null);
    })();
  };

  const closeTab = (id: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== id);
      if (activeId === id) setActiveId(next[0]?.id ?? null);
      return next;
    });
  };

  const onResumed = async (agent: string, sessionId: string) => {
    const existing = tabs.find((t) => t.sessionId === sessionId);
    if (existing) {
      setActiveId(existing.id);
    } else {
      let defaults: AgentDefaults | undefined;
      try {
        defaults = await fetchAgentDefaults(agent);
      } catch {
        defaults = undefined;
      }
      const t = newTab(agent, defaults, sessionId);
      setTabs((prev) => [...prev, t]);
      setActiveId(t.id);
    }
    try {
      const turns = await fetchSessionTurns(sessionId);
      setHydratedLog(turnsToLog(turns));
    } catch {
      setHydratedLog(null);
    }
  };

  const activeHealth = activeTab ? healthFor(activeTab.agent, activeTab.harness) : undefined;

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      <ChatSidebar
        tabs={tabs}
        activeId={activeId}
        agents={agents}
        healthByAgent={healthByAgent}
        onSelect={(id) => {
          setActiveId(id);
          setHydratedLog(null);
        }}
        onClose={closeTab}
        onNew={addTab}
        onResumed={onResumed}
      />
      <main className="flex min-w-0 flex-1 flex-col bg-background">
        {defaultsWarning ? (
          <p className="shrink-0 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-900 dark:text-amber-100">
            {defaultsWarning}
          </p>
        ) : null}
        {agentsError ? (
          <div className="flex flex-1 items-center justify-center px-6">
            <p className="text-sm text-destructive" role="alert">
              Failed to load agents. Chat is unavailable until the roster can be fetched.
            </p>
          </div>
        ) : agentsLoading ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Loading agents…
          </div>
        ) : activeTab ? (
          <ChatPane
            key={`${activeTab.id}-${hydratedLog ? "h" : "f"}`}
            tab={activeTab}
            health={activeHealth}
            initialLog={hydratedLog ?? undefined}
            dbDefaults={dbDefaults}
            onUpdate={(patch) => updateTab(activeTab.id, patch)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            No active chat
          </div>
        )}
      </main>
      <CockpitContextPanel agent={activeTab?.agent ?? null} health={activeHealth} />
    </div>
  );
}
