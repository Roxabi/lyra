import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChatPane } from "@/components/ChatPane";
import { MultiChatTabs } from "@/components/MultiChatTabs";
import { PanelMount } from "@/components/PanelMount";
import { ReprendrePanel } from "@/components/ReprendrePanel";
import { fetchAgentStatus, fetchAgents } from "@/lib/api";
import { type ChatTab, loadTabs, newTab, saveTabs } from "@/lib/chats-storage";

export function CockpitLayout() {
  const [tabs, setTabs] = useState<ChatTab[]>(() => loadTabs());
  const [activeId, setActiveId] = useState<string | null>(() => loadTabs()[0]?.id ?? null);

  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const activeTab = useMemo(() => tabs.find((t) => t.id === activeId) ?? tabs[0], [tabs, activeId]);

  const { data: status = [] } = useQuery({
    queryKey: ["agent-status", activeTab?.agent, activeTab?.harness],
    queryFn: () =>
      fetchAgentStatus(activeTab?.agent, activeTab?.harness),
    enabled: Boolean(activeTab?.agent),
    refetchInterval: 30_000,
  });

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

  const healthFor = useCallback(
    (agent: string, harness?: ChatTab["harness"]) =>
      status.find((h) => h.agent === agent && (!harness || h.harness === harness)),
    [status],
  );

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
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-[12rem_1fr_var(--sidebar-w)] gap-3 p-3">
      <aside className="min-h-0 rounded-lg border border-border bg-card/50 p-2">
        <MultiChatTabs
          tabs={tabs}
          activeId={activeId}
          onSelect={setActiveId}
          onClose={closeTab}
          onNew={addTab}
        />
      </aside>
      <main className="min-h-0 rounded-lg border border-border bg-card/30 p-3">
        {activeTab ? (
          <ChatPane
            tab={activeTab}
            health={healthFor(activeTab.agent, activeTab.harness)}
            onUpdate={(patch) => updateTab(activeTab.id, patch)}
          />
        ) : (
          <p className="text-sm text-muted-foreground">No active chat.</p>
        )}
      </main>
      <aside className="flex min-h-0 flex-col gap-3 overflow-auto rounded-lg border border-border bg-card/50 p-3">
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
        <PanelMount id="jobs" title="Jobs (#1772)" disabled />
        <PanelMount id="obs" title="Obs (#1774)" disabled />
      </aside>
    </div>
  );
}
