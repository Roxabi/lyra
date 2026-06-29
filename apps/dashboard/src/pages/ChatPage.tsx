import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChatPane } from "@/components/ChatPane";
import { ChatSidebar } from "@/components/layout/ChatSidebar";
import { CockpitContextPanel } from "@/components/layout/CockpitContextPanel";
import { useAgentStatus } from "@/hooks/useAgentStatus";
import { fetchAgentDefaults } from "@/lib/agents-api";
import { fetchAgents, fetchSessionTurns } from "@/lib/api";
import { turnsToLog } from "@/lib/chat-messages";
import { type AgentDefaults, type ChatTab, loadTabs, newTab, saveTabs } from "@/lib/chats-storage";

export function ChatPage() {
  const { t } = useTranslation("chat");
  const [tabs, setTabs] = useState<ChatTab[]>(() => loadTabs());
  const [activeId, setActiveId] = useState<string | null>(() => loadTabs()[0]?.id ?? null);
  const [hydratedLog, setHydratedLog] = useState<string | null>(null);

  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const activeTab = useMemo(() => tabs.find((t) => t.id === activeId) ?? tabs[0], [tabs, activeId]);
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
    const existing = tabs.find((t) => t.agent === agent);
    if (existing) {
      setActiveId(existing.id);
      return;
    }
    void (async () => {
      let defaults: AgentDefaults | undefined;
      try {
        defaults = await fetchAgentDefaults(agent);
      } catch {
        defaults = undefined;
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
    const existing = tabs.find((t) => t.agent === agent);
    if (existing) setActiveId(existing.id);
    else {
      let defaults: AgentDefaults | undefined;
      try {
        defaults = await fetchAgentDefaults(agent);
      } catch {
        defaults = undefined;
      }
      const t = newTab(agent, defaults);
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
        {activeTab ? (
          <ChatPane
            key={`${activeTab.id}-${hydratedLog ? "h" : "f"}`}
            tab={activeTab}
            health={activeHealth}
            initialLog={hydratedLog ?? undefined}
            onUpdate={(patch) => updateTab(activeTab.id, patch)}
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
