import type { AgentInfo } from "../../types";

/**
 * Presentation-only mapping for the three runtime agent modes.
 *
 * Runtime IDs (`fast` / `search` / `team`) are a backend contract — they are
 * sent as `?agent_id=` and stored in session metadata, so they must never be
 * renamed here. This module only decides how each mode LOOKS.
 *
 * Display names come from i18n (`agents.<id>.name`) or admin catalog labels;
 * this module deliberately does not hardcode them.
 */

export const AGENT_MODE_IDS = ["search", "fast", "team"] as const;
export type AgentModeId = (typeof AGENT_MODE_IDS)[number];

/** Lucide icon name per mode, used when the admin catalog has no icon set. */
export const AGENT_MODE_FALLBACK_ICON: Record<AgentModeId, string> = {
  search: "Briefcase",
  fast: "Zap",
  team: "Users",
};

/**
 * Canonical ordering for the home-page mode pills: 日常办公 → 快速问答 → 团队协作.
 * Unknown/custom agents keep their catalog sort_order and land after these.
 */
const MODE_ORDER: Record<string, number> = {
  search: 0,
  fast: 1,
  team: 2,
};

export function isKnownAgentMode(id: string): id is AgentModeId {
  return (AGENT_MODE_IDS as readonly string[]).includes(id);
}

export function resolveAgentModeIcon(agent: Pick<AgentInfo, "id" | "icon">) {
  if (agent.icon && agent.icon !== "Bot") return agent.icon;
  if (isKnownAgentMode(agent.id)) return AGENT_MODE_FALLBACK_ICON[agent.id];
  return "Bot";
}

/**
 * Order agents for the mode switcher. Never filters: the caller already
 * receives only the modes this user is allowed to see from `/api/agents`.
 */
export function sortAgentModes<T extends Pick<AgentInfo, "id">>(
  agents: readonly T[],
): T[] {
  return [...agents].sort((a, b) => {
    const ra = MODE_ORDER[a.id] ?? Number.MAX_SAFE_INTEGER;
    const rb = MODE_ORDER[b.id] ?? Number.MAX_SAFE_INTEGER;
    if (ra !== rb) return ra - rb;
    return a.id.localeCompare(b.id);
  });
}
