/** Strip legacy `_default` suffix and title-case for display (lyra_default → Lyra). */
export function displayAgentName(agentId: string): string {
  const base = agentId.replace(/_default$/i, "").trim();
  if (!base) return agentId;
  return base.charAt(0).toUpperCase() + base.slice(1);
}

/** Normalize roster id for API calls (Lyra → lyra, lyra_default → lyra). */
export function normalizeAgentId(agentId: string): string {
  return agentId.replace(/_default$/i, "").toLowerCase();
}
