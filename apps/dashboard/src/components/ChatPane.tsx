import { useCallback, useEffect, useRef, useState } from "react";
import { AgentStatusBadge } from "@/components/AgentStatusBadge";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { MessageList } from "@/components/chat/MessageList";
import { HarnessPicker } from "@/components/HarnessPicker";
import { ModelPicker } from "@/components/ModelPicker";
import { displayAgentName } from "@/lib/agents";
import type { AgentHealth } from "@/lib/api";
import { defaultModelForHarness, openChatStream, postChat } from "@/lib/api";
import type { AgentDefaults, ChatTab } from "@/lib/chats-storage";

interface ChatPaneProps {
  tab: ChatTab;
  health: AgentHealth | undefined;
  initialLog?: string;
  onUpdate: (patch: Partial<ChatTab>) => void;
  dbDefaults?: AgentDefaults;
}

export function ChatPane({ tab, health, initialLog, onUpdate, dbDefaults }: ChatPaneProps) {
  const [text, setText] = useState("");
  const [log, setLog] = useState(initialLog ?? "");
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  const connectStream = useCallback((sessionId: string, streamToken: string) => {
    sourceRef.current?.close();
    sourceRef.current = openChatStream(sessionId, streamToken, (ev) => {
      if (ev.type === "delta") setLog((l) => l + (ev.text ?? ""));
      if (ev.type === "done") setLog((l) => `${l}\n---\n`);
      if (ev.type === "error") setLog((l) => `${l}\n[error] ${ev.message ?? ""}\n`);
    });
  }, []);

  useEffect(() => {
    return () => sourceRef.current?.close();
  }, []);

  const send = async () => {
    if (!text.trim()) return;
    setError(null);
    try {
      const res = await postChat({
        agent: tab.agent,
        text,
        session_id: tab.sessionId,
        harness: tab.harness,
        model: tab.model,
      });
      onUpdate({
        sessionId: res.session_id,
        streamToken: res.stream_token,
        lastActive: Date.now(),
      });
      connectStream(res.session_id, res.stream_token);
      setLog((l) => `${l}> ${text}\n`);
      setText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "send failed");
    }
  };

  const offline = health?.online === false;
  const label = displayAgentName(tab.agent);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-3 px-5 py-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex size-8 items-center justify-center rounded-lg bg-brand/15 text-xs font-semibold text-brand">
            {label.slice(0, 1)}
          </div>
          <div className="min-w-0">
            <p className="truncate font-[family-name:var(--font-head)] text-sm font-semibold">
              {label}
            </p>
            <p className="text-xs text-muted-foreground">Session opérateur</p>
          </div>
          <AgentStatusBadge health={health} />
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <HarnessPicker
            value={tab.harness}
            dbDefault={dbDefaults?.backend}
            onChange={(h) => onUpdate({ harness: h, model: defaultModelForHarness(h) })}
            disabled={offline}
          />
          <ModelPicker
            harness={tab.harness}
            value={tab.model}
            onChange={(m) => onUpdate({ model: m })}
            offline={offline}
          />
        </div>
      </header>

      <MessageList log={log} agent={label} offline={offline} />

      {error ? (
        <p className="shrink-0 px-5 pb-1 text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      <ChatComposer value={text} disabled={offline} onChange={setText} onSend={send} />
    </div>
  );
}
