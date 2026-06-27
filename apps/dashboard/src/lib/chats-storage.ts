export type HarnessKind = "claude-cli" | "omp-rpc";

export interface ChatTab {
  id: string;
  agent: string;
  harness: HarnessKind;
  model: string;
  sessionId: string | null;
  streamToken: string | null;
  lastActive: number;
}

const STORAGE_KEY = "factory.dashboard.chats.v1";

export function loadTabs(): ChatTab[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ChatTab[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveTabs(tabs: ChatTab[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs));
}

export function newTab(agent: string): ChatTab {
  return {
    id: crypto.randomUUID(),
    agent,
    harness: "claude-cli",
    model: "sonnet",
    sessionId: null,
    streamToken: null,
    lastActive: Date.now(),
  };
}
