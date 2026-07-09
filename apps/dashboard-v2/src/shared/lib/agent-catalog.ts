import { displayAgentName, normalizeAgentId } from "@/shared/lib/agents";

export type AgentAccent = "brand" | "amber" | "sky";

export interface AgentPersona {
  id: string;
  displayName: string;
  tagline: string;
  accent: AgentAccent;
  initial: string;
}

const PERSONAS: Record<string, Omit<AgentPersona, "id">> = {
  lyra: {
    displayName: "Lyra",
    tagline: "Ops console",
    accent: "brand",
    initial: "L",
  },
  aryl: {
    displayName: "Aryl",
    tagline: "Field agent",
    accent: "amber",
    initial: "A",
  },
};

export function getAgentPersona(agentId: string): AgentPersona {
  const id = normalizeAgentId(agentId);
  const known = PERSONAS[id];
  const displayName = known?.displayName ?? displayAgentName(agentId);
  return {
    id,
    displayName,
    tagline: known?.tagline ?? "Agent",
    accent: known?.accent ?? "sky",
    initial: known?.initial ?? displayName.slice(0, 1).toUpperCase(),
  };
}

export const agentAccentClass: Record<AgentAccent, string> = {
  brand: "bg-primary/20 text-primary",
  amber: "bg-amber-500/20 text-amber-600 dark:text-amber-400",
  sky: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
};
