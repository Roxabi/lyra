import { WarningCircle } from "@phosphor-icons/react";
import type { ChatMessage } from "@/lib/chat-messages";
import { cn } from "@/lib/utils";

interface MessageBubbleProps {
  message: ChatMessage;
  agentLabel: string;
}

export function MessageBubble({ message, agentLabel }: MessageBubbleProps) {
  if (message.role === "error") {
    return (
      <div className="flex justify-center">
        <div className="inline-flex max-w-[90%] items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <WarningCircle className="size-3.5 shrink-0" aria-hidden />
          <span>{message.content}</span>
        </div>
      </div>
    );
  }

  const isUser = message.role === "user";

  return (
    <div className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}>
      <div
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold uppercase",
          isUser ? "bg-brand/20 text-brand" : "border border-border bg-card text-muted-foreground",
        )}
        aria-hidden
      >
        {isUser ? "U" : agentLabel.slice(0, 1)}
      </div>
      <div
        className={cn(
          "max-w-[min(72ch,85%)] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
          isUser
            ? "bg-brand text-brand-foreground"
            : "border border-border/80 bg-card text-foreground",
        )}
      >
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
    </div>
  );
}
