import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { ChatPane } from "@/components/ChatPane";
import { MultiChatTabs } from "@/components/MultiChatTabs";
import { PanelMount } from "@/components/PanelMount";
import { ReprendrePanel } from "@/components/ReprendrePanel";
import { useAgentStatus } from "@/hooks/useAgentStatus";
import { fetchAgents } from "@/lib/api";
import { type ChatTab, loadTabs, newTab, saveTabs } from "@/lib/chats-storage";

export function CockpitLayout() {
  const [tabs, setTabs] = useState<ChatTab[]>(() => loadTabs());
  const [activeId, setActiveId] = useState<string | null>(() => loadTabs()[0]?.id ?? null);

  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const activeTab = useMemo(() => tabs.find((t) => t.id === activeId) ?? tabs[0], [tabs, activeId]);
  const { healthFor } = useAgentStatus(activeTab);

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

  const addTab = () => {
    const agent = agents[0] ?? "lyra";
    const t = newTab(agent);
    setTabs((prev) => [...prev, t]);
    setActiveId(t.id);
  };

  const closeTab = (id: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== id);
      if (activeId === id) setActiveId(next[0]?.id ?? null);
      return next;
    });
  };

  return (
    <div className="flex h-[calc(100vh-var(--header-h))] min-h-0">
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-card">
        <MultiChatTabs
          tabs={tabs}
          activeId={activeId}
          onSelect={setActiveId}
          onClose={closeTab}
          onNew={addTab}
        />
      </aside>

      <main className="flex min-w-0 flex-1 flex-col bg-background">
        {activeTab ? (
          <ChatPane
            tab={activeTab}
            health={healthFor(activeTab.agent, activeTab.harness)}
            onUpdate={(patch) => updateTab(activeTab.id, patch)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Aucun chat actif.
          </div>
        )}
      </main>

      <aside className="fd-scroll flex w-[var(--sidebar-w)] shrink-0 flex-col gap-4 overflow-y-auto border-l border-border bg-card px-4 py-4">
        <ReprendrePanel
          agent={activeTab?.agent ?? null}
          onResumed={(agent) => {
            const existing = tabs.find((t) => t.agent === agent);
            if (existing) setActiveId(existing.id);
            else {
              const t = newTab(agent);
              setTabs((prev) => [...prev, t]);
              setActiveId(t.id);
            }
          }}
        />
        <div className="space-y-2 border-t border-border/60 pt-4">
          <p className="text-xs font-medium text-muted-foreground">À venir</p>
          <PanelMount id="jobs" title="Jobs (#1772)" disabled />
          <PanelMount id="obs" title="Obs (#1774)" disabled />
        </div>
      </aside>
    </div>
  );
}
