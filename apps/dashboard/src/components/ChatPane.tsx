import { useCallback, useEffect, useRef, useState } from "react";
import { AgentStatusBadge } from "@/components/AgentStatusBadge";
import { HarnessPicker } from "@/components/HarnessPicker";
import { ModelPicker } from "@/components/ModelPicker";
import { Button } from "@/components/ui/button";
import type { AgentHealth } from "@/lib/api";
import { defaultModelForHarness, openChatStream, postChat } from "@/lib/api";
import type { ChatTab } from "@/lib/chats-storage";

interface ChatPaneProps {
  tab: ChatTab;
  health: AgentHealth | undefined;
  onUpdate: (patch: Partial<ChatTab>) => void;
}

export function ChatPane({ tab, health, onUpdate }: ChatPaneProps) {
  const [text, setText] = useState("");
  const [log, setLog] = useState("");
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

  const offline = health ? !health.online : false;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 border-b border-border pb-2">
        <span className="font-[family-name:var(--font-head)] text-sm font-bold">{tab.agent}</span>
        <AgentStatusBadge health={health} />
        <HarnessPicker
          value={tab.harness}
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
      <pre className="min-h-0 flex-1 overflow-auto rounded-md border border-border bg-card p-3 text-sm whitespace-pre-wrap">
        {log || "Start a conversation…"}
      </pre>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      <div className="flex gap-2">
        <input
          className="flex-1 rounded border border-border bg-background px-3 py-2 text-sm"
          value={text}
          placeholder="Message"
          disabled={offline}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
        />
        <Button onClick={send} disabled={offline}>
          Send
        </Button>
      </div>
    </div>
  );
}
