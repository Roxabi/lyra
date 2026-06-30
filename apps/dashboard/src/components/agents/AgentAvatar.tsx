import { agentAccentClass, getAgentPersona } from "@/lib/agent-catalog";
import { cn } from "@/lib/utils";

const SIZES = {
  sm: "size-6 rounded-md text-[10px]",
  md: "size-8 rounded-lg text-xs",
  lg: "size-10 rounded-xl text-sm",
} as const;

interface AgentAvatarProps {
  agentId: string;
  size?: keyof typeof SIZES;
  className?: string;
}

export function AgentAvatar({ agentId, size = "md", className }: AgentAvatarProps) {
  const persona = getAgentPersona(agentId);
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center font-semibold",
        SIZES[size],
        agentAccentClass[persona.accent],
        className,
      )}
      aria-hidden
    >
      {persona.initial}
    </span>
  );
}
