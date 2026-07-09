import type { UIMessage } from "@tanstack/ai/client";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchAgentDefaults } from "@/features/agents/api";
import { fetchAgents, fetchSessionTurns } from "@/features/chat/api";
import { ChatPane } from "@/features/chat/components/chat-pane";
import { ChatSidebar } from "@/features/chat/components/chat-sidebar";
import { CockpitContextPanel } from "@/features/chat/components/cockpit-context-panel";
import { useAgentStatus } from "@/shared/hooks/use-agent-status";
import { turnsToInitialMessages } from "@/shared/lib/chat-messages";
import {
  type AgentDefaults,
  type ChatTab,
  loadTabs,
  newTab,
  saveTabs,
} from "@/shared/lib/chats-storage";

export function ChatPage() {
  const { t } = useTranslation("chat");
  const [tabs, setTabs] = useState<ChatTab[]>(() => loadTabs());
  const [activeId, setActiveId] = useState<string | null>(() => loadTabs()[0]?.id ?? null);
  const [messagesByTab, setMessagesByTab] = useState<Record<string, UIMessage[]>>({});
  const [resumeHydration, setResumeHydration] = useState<Record<string, UIMessage[]>>({});
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
          setDefaultsWarning(t("defaultsWarning", { agent: agents[0] }));
        }
        const tab = newTab(agents[0], defaults);
        setTabs([tab]);
        setActiveId(tab.id);
      })();
    }
  }, [agents, tabs.length, t]);

  const updateTab = (id: string, patch: Partial<ChatTab>) => {
    setTabs((prev) => prev.map((tab) => (tab.id === id ? { ...tab, ...patch } : tab)));
  };

  const addTab = (agent: string) => {
    void (async () => {
      let defaults: AgentDefaults | undefined;
      try {
        defaults = await fetchAgentDefaults(agent);
      } catch {
        defaults = undefined;
        setDefaultsWarning(t("defaultsWarning", { agent }));
      }
      const tab = newTab(agent, defaults);
      setTabs((prev) => [...prev, tab]);
      setActiveId(tab.id);
    })();
  };

  const closeTab = (id: string) => {
    setTabs((prev) => {
      const next = prev.filter((tab) => tab.id !== id);
      if (activeId === id) setActiveId(next[0]?.id ?? null);
      return next;
    });
    setMessagesByTab((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    setResumeHydration((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  const onResumed = async (agent: string, sessionId: string) => {
    let tabId: string;
    const existing = tabs.find((tab) => tab.sessionId === sessionId);
    if (existing) {
      tabId = existing.id;
      setActiveId(existing.id);
    } else {
      let defaults: AgentDefaults | undefined;
      try {
        defaults = await fetchAgentDefaults(agent);
      } catch {
        defaults = undefined;
      }
      const tab = newTab(agent, defaults, sessionId);
      tabId = tab.id;
      setTabs((prev) => [...prev, tab]);
      setActiveId(tab.id);
    }
    try {
      const turns = await fetchSessionTurns(sessionId);
      setResumeHydration((prev) => ({
        ...prev,
        [tabId]: turnsToInitialMessages(turns),
      }));
    } catch {
      setResumeHydration((prev) => {
        const next = { ...prev };
        delete next[tabId];
        return next;
      });
    }
  };

  const onMessagesUpdate = useCallback((tabId: string, messages: UIMessage[]) => {
    setMessagesByTab((prev) => ({ ...prev, [tabId]: messages }));
    setResumeHydration((prev) => {
      if (!prev[tabId]) return prev;
      const next = { ...prev };
      delete next[tabId];
      return next;
    });
  }, []);

  const activeHealth = activeTab ? healthFor(activeTab.agent, activeTab.harness) : undefined;
  const paneInitialMessages = activeTab
    ? (resumeHydration[activeTab.id] ?? messagesByTab[activeTab.id])
    : undefined;

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      <ChatSidebar
        tabs={tabs}
        activeId={activeId}
        agents={agents}
        healthByAgent={healthByAgent}
        onSelect={setActiveId}
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
              {t("agentsLoadError")}
            </p>
          </div>
        ) : agentsLoading ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            {t("loadingAgents")}
          </div>
        ) : activeTab ? (
          <ChatPane
            key={activeTab.id}
            tab={activeTab}
            health={activeHealth}
            initialMessages={paneInitialMessages}
            dbDefaults={dbDefaults}
            onUpdate={(patch) => updateTab(activeTab.id, patch)}
            onMessagesUpdate={(messages) => onMessagesUpdate(activeTab.id, messages)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            {t("empty")}
          </div>
        )}
      </main>
      <CockpitContextPanel agent={activeTab?.agent ?? null} health={activeHealth} />
    </div>
  );
}
