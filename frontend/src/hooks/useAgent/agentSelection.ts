import type { AgentInfo } from "../../types";
import type { PreferredAgentId } from "../../types/personaPreset";

const PREFERRED_AGENT_IDS = new Set<PreferredAgentId>(["fast", "search"]);

export function resolveAvailableAgentId(
  currentAgentId: string,
  preferredDefaultAgentId: string | undefined,
  agents: AgentInfo[],
): string {
  const availableIds = new Set(agents.map((agent) => agent.id));

  if (currentAgentId && availableIds.has(currentAgentId)) {
    return currentAgentId;
  }

  if (preferredDefaultAgentId && availableIds.has(preferredDefaultAgentId)) {
    return preferredDefaultAgentId;
  }

  return agents[0]?.id || "";
}

/**
 * Resolve the agent id for a persona-bound chat.
 * Matches backend resolve_persona_agent_id: preferred wins when valid,
 * otherwise requested, otherwise fast.
 *
 * Signature: (preferred, requested?) — preferred is persona.preferred_agent_id.
 */
export function resolvePersonaAgentId(
  preferredAgentId?: string | null,
  requestedAgentId?: string | null,
): PreferredAgentId {
  if (preferredAgentId && PREFERRED_AGENT_IDS.has(preferredAgentId as PreferredAgentId)) {
    return preferredAgentId as PreferredAgentId;
  }
  if (requestedAgentId && PREFERRED_AGENT_IDS.has(requestedAgentId as PreferredAgentId)) {
    return requestedAgentId as PreferredAgentId;
  }
  return "fast";
}
