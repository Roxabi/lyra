import type { UIMessage } from "@tanstack/ai/client";
import { useEffect, useRef } from "react";
import { ChatEmptyState } from "@/features/chat/components/chat-empty-state";
import { MessageBubble } from "@/features/chat/components/message-bubble";

interface MessageListProps {
  messages: UIMessage[];
  agent: string;
  offline: boolean;
}

export function MessageList({ messages, agent, offline }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll when transcript grows
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return <ChatEmptyState agent={agent} offline={offline} />;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-5">
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} agentLabel={agent} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
