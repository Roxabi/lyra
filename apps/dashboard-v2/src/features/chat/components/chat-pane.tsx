import type { UIMessage } from "@tanstack/ai/client";
import { useChat } from "@tanstack/ai-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { makeAguiAdapter } from "@/features/chat/agui-adapter";
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
  initialMessages?: UIMessage[];
  onUpdate: (patch: Partial<ChatTab>) => void;
  dbDefaults?: AgentDefaults;
}

export function ChatPane({ tab, health, initialMessages, onUpdate, dbDefaults }: ChatPaneProps) {
  const [text, setText] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const tabRef = useRef(tab);
  tabRef.current = tab;

  const onSession = useCallback(
    (patch: { sessionId: string; streamToken: string }) => {
      onUpdate({
        sessionId: patch.sessionId,
        streamToken: patch.streamToken,
        lastActive: Date.now(),
      });
    },
    [onUpdate],
  );

  const connection = useMemo(
    () =>
      makeAguiAdapter(
        () => ({
          tabId: tabRef.current.id,
          agent: tabRef.current.agent,
          harness: tabRef.current.harness,
          model: tabRef.current.model,
          sessionId: tabRef.current.sessionId,
        }),
        onSession,
      ),
    [onSession],
  );

  const { messages, sendMessage, isLoading, error, stop } = useChat({
    connection,
    initialMessages,
    threadId: tab.id,
    onError: (err) => setSendError(err.message),
  });

  const send = async () => {
    if (!text.trim() || isLoading) return;
    setSendError(null);
    try {
      await sendMessage(text.trim());
      setText("");
    } catch (e) {
      setSendError(e instanceof Error ? e.message : "send failed");
    }
  };

  const offline = health?.online === false;
  const label = displayAgentName(tab.agent);
  const displayError = sendError ?? error?.message ?? null;

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

      <MessageList messages={messages} agent={label} offline={offline} />

      {displayError ? (
        <p className="shrink-0 px-5 pb-1 text-xs text-destructive" role="alert">
          {displayError}
        </p>
      ) : null}

      <ChatComposer
        value={text}
        disabled={offline}
        isLoading={isLoading}
        onChange={setText}
        onSend={send}
        onStop={stop}
      />
    </div>
  );
}
