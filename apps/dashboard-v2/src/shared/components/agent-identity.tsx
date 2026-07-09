import { cn } from "@/lib/utils";
import { AgentAvatar } from "@/shared/components/agent-avatar";
import { getAgentPersona } from "@/shared/lib/agent-catalog";

interface AgentIdentityProps {
  agentId: string;
  subtitle?: string;
  showAvatar?: boolean;
  avatarSize?: "sm" | "md" | "lg";
  className?: string;
  nameClassName?: string;
}

export function AgentIdentity({
  agentId,
  subtitle,
  showAvatar = true,
  avatarSize = "md",
  className,
  nameClassName,
}: AgentIdentityProps) {
  const persona = getAgentPersona(agentId);
  const sub = subtitle ?? persona.tagline;

  return (
    <div className={cn("flex min-w-0 items-center gap-2.5", className)}>
      {showAvatar ? <AgentAvatar agentId={agentId} size={avatarSize} /> : null}
      <div className="min-w-0">
        <p className={cn("truncate font-medium text-foreground", nameClassName)}>
          {persona.displayName}
        </p>
        {sub ? <p className="truncate text-xs text-muted-foreground">{sub}</p> : null}
      </div>
    </div>
  );
}
