/** UUID v4 — works on plain HTTP (crypto.randomUUID is secure-context only). */
export function randomId(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

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

const STORAGE_KEY = "factory.dashboard.chats.v2";

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

export interface AgentDefaults {
  backend: HarnessKind;
  model: string;
}

export function newTab(
  agent: string,
  defaults?: AgentDefaults,
  sessionId: string | null = null,
): ChatTab {
  return {
    id: randomId(),
    agent,
    harness: defaults?.backend ?? "claude-cli",
    model: defaults?.model ?? "sonnet",
    sessionId,
    streamToken: null,
    lastActive: Date.now(),
  };
}
