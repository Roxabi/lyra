import { displayAgentName, normalizeAgentId } from "@/lib/agents";

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
  brand: "bg-brand/20 text-brand",
  amber: "bg-status-closing/20 text-status-closing",
  sky: "bg-primary/15 text-primary",
};
