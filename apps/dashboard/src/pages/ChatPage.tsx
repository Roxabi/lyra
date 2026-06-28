import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { ChatPane } from "@/components/ChatPane";
import { ChatSidebar } from "@/components/layout/ChatSidebar";
import { useAgentStatus } from "@/hooks/useAgentStatus";
import { fetchAgents, fetchSessionTurns } from "@/lib/api";
import { turnsToLog } from "@/lib/chat-messages";
import { type ChatTab, loadTabs, newTab, saveTabs } from "@/lib/chats-storage";

export function ChatPage() {
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
      const t = newTab(agents[0]);
      setTabs([t]);
      setActiveId(t.id);
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
    const t = newTab(agent);
    setTabs((prev) => [...prev, t]);
    setActiveId(t.id);
    setHydratedLog(null);
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
      const t = newTab(agent);
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

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
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
            health={healthFor(activeTab.agent, activeTab.harness)}
            initialLog={hydratedLog ?? undefined}
            onUpdate={(patch) => updateTab(activeTab.id, patch)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Créez un chat pour commencer.
          </div>
        )}
      </main>
    </div>
  );
}
