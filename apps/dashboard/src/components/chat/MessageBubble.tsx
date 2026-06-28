import { WarningCircle } from "@phosphor-icons/react";
import type { ChatMessage } from "@/lib/chat-messages";
import { cn } from "@/lib/utils";

interface MessageBubbleProps {
  message: ChatMessage;
  agentLabel: string;
}

function renderInlineMarkdown(text: string) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return parts.map((part) => {
    const key = `${part.slice(0, 12)}-${part.length}`;
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={key} className="rounded bg-background/60 px-1 py-0.5 font-mono text-[0.85em]">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    return <span key={key}>{part}</span>;
  });
}

export function MessageBubble({ message, agentLabel }: MessageBubbleProps) {
  if (message.role === "error") {
    return (
      <div className="flex justify-center">
        <div className="inline-flex max-w-[90%] items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
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
          isUser ? "bg-brand/20 text-brand" : "bg-muted text-muted-foreground",
        )}
        aria-hidden
      >
        {isUser ? "U" : agentLabel.slice(0, 1)}
      </div>
      <div
        className={cn(
          "max-w-[min(72ch,85%)] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
          isUser ? "bg-brand text-brand-foreground" : "bg-muted/50 text-foreground",
        )}
      >
        <p className="whitespace-pre-wrap break-words">{renderInlineMarkdown(message.content)}</p>
      </div>
    </div>
  );
}
