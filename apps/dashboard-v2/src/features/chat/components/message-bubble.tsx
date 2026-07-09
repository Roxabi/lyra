import type { MessagePart, UIMessage } from "@tanstack/ai/client";
import { AlertCircle, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

interface MessageBubbleProps {
  message: UIMessage;
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

function ThinkingBlock({ content }: { content: string }) {
  const { t } = useTranslation("chat");
  if (!content.trim()) return null;
  return (
    <details className="rounded-lg border border-border/60 bg-background/40 px-3 py-2 text-xs text-muted-foreground">
      <summary className="cursor-pointer font-medium">{t("message.thinking")}</summary>
      <p className="mt-2 whitespace-pre-wrap break-words italic leading-relaxed">{content}</p>
    </details>
  );
}

function ToolCallBlock({ part }: { part: Extract<MessagePart, { type: "tool-call" }> }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-border/60 bg-background/50 px-3 py-2 text-xs">
      <Wrench className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0">
        <p className="font-medium text-foreground">{part.name}</p>
        {part.arguments ? (
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[0.8em] text-muted-foreground">
            {part.arguments}
          </pre>
        ) : null}
        {part.output !== undefined ? (
          <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
            {typeof part.output === "string" ? part.output : JSON.stringify(part.output)}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function TextBlock({ content }: { content: string }) {
  if (!content.trim()) return null;
  return (
    <p className="whitespace-pre-wrap break-words leading-relaxed">
      {renderInlineMarkdown(content)}
    </p>
  );
}

export function MessageBubble({ message, agentLabel }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}>
      <div
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold uppercase",
          isUser ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground",
        )}
        aria-hidden
      >
        {isUser ? "U" : agentLabel.slice(0, 1)}
      </div>
      <div
        className={cn(
          "flex max-w-[min(72ch,85%)] flex-col gap-2 rounded-2xl px-3.5 py-2.5 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted/50 text-foreground",
        )}
      >
        {message.parts.map((part) => {
          if (part.type === "text") {
            const key = `${message.id}-text-${part.content.length}-${part.content.slice(0, 24)}`;
            return <TextBlock key={key} content={part.content} />;
          }
          if (part.type === "thinking") {
            const key = `${message.id}-think-${part.content.length}-${part.content.slice(0, 24)}`;
            return <ThinkingBlock key={key} content={part.content} />;
          }
          if (part.type === "tool-call") {
            return <ToolCallBlock key={part.id} part={part} />;
          }
          if (part.type === "tool-result") {
            const content =
              typeof part.content === "string"
                ? part.content
                : part.content
                    .filter((block) => block.type === "text")
                    .map((block) => block.content)
                    .join("\n");
            const key = `${message.id}-tool-result-${part.toolCallId}`;
            if (part.error) {
              return (
                <div
                  key={key}
                  className="inline-flex items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive"
                >
                  <AlertCircle className="size-3.5 shrink-0" aria-hidden />
                  <span>{part.error}</span>
                </div>
              );
            }
            return <TextBlock key={key} content={content} />;
          }
          return null;
        })}
      </div>
    </div>
  );
}
