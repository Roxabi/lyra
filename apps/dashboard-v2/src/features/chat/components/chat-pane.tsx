import { useCallback, useEffect, useRef, useState } from "react";
import { openChatStream, postChat } from "@/features/chat/api";
import { ChatComposer } from "@/features/chat/components/chat-composer";
import { MessageList } from "@/features/chat/components/message-list";
import type { AgentHealth } from "@/shared/api/bff-types";
import { defaultModelForHarness } from "@/shared/api/bff-types";
import { AgentIdentity } from "@/shared/components/agent-identity";
import { AgentStatusBadge } from "@/shared/components/agent-status-badge";
import { HarnessPicker } from "@/shared/components/harness-picker";
import { ModelPicker } from "@/shared/components/model-picker";
import { displayAgentName } from "@/shared/lib/agents";
import type { AgentDefaults, ChatTab } from "@/shared/lib/chats-storage";

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
        <div className="flex min-w-0 items-center gap-2">
          <AgentIdentity
            agentId={tab.agent}
            subtitle={`${tab.harness} · ${tab.model}`}
            nameClassName="text-sm font-semibold"
          />
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
