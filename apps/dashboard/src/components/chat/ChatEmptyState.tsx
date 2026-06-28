import { ChatCircleDots } from "@phosphor-icons/react";

interface ChatEmptyStateProps {
  agent: string;
  offline: boolean;
}

export function ChatEmptyState({ agent, offline }: ChatEmptyStateProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      <div className="flex size-14 items-center justify-center rounded-2xl border border-border bg-card shadow-sm">
        <ChatCircleDots className="size-7 text-muted-foreground" weight="duotone" aria-hidden />
      </div>
      <div className="max-w-sm space-y-1.5">
        <p className="font-[family-name:var(--font-head)] text-base font-semibold text-foreground">
          Conversation avec {agent}
        </p>
        <p className="text-sm text-muted-foreground">
          {offline
            ? "L'agent est hors ligne. Vérifiez le harness ou réessayez plus tard."
            : "Envoyez un message pour démarrer. Les réponses s'affichent ici en temps réel."}
        </p>
      </div>
    </div>
  );
}
