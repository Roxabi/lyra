export type MessageRole = "user" | "assistant" | "error";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
}

function pushMessage(messages: ChatMessage[], role: MessageRole, content: string) {
  const trimmed = content.trim();
  if (!trimmed) return;
  messages.push({ id: `${role}-${messages.length}`, role, content: trimmed });
}

function parseAssistantTail(raw: string, messages: ChatMessage[]) {
  const errorIdx = raw.lastIndexOf("\n[error] ");
  if (errorIdx >= 0) {
    pushMessage(messages, "assistant", raw.slice(0, errorIdx));
    pushMessage(messages, "error", raw.slice(errorIdx + "\n[error] ".length));
    return;
  }
  if (raw.startsWith("[error] ")) {
    pushMessage(messages, "error", raw.slice("[error] ".length));
    return;
  }
  pushMessage(messages, "assistant", raw);
}

export function parseChatLog(log: string): ChatMessage[] {
  if (!log.trim()) return [];

  const messages: ChatMessage[] = [];
  const blocks = log.split(/\n---\n/);

  for (const block of blocks) {
    const trimmed = block.trim();
    if (!trimmed) continue;

    if (trimmed.startsWith("> ")) {
      const newline = trimmed.indexOf("\n");
      const userLine = newline === -1 ? trimmed : trimmed.slice(0, newline);
      pushMessage(messages, "user", userLine.slice(2));
      if (newline !== -1) parseAssistantTail(trimmed.slice(newline + 1), messages);
      continue;
    }

    parseAssistantTail(trimmed, messages);
  }

  return messages;
}
