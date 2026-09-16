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

/**
 * Canonical ordering for the home-page mode pills: 日常办公 → 快速问答 → 团队协作.
 * Known modes use this fixed product order; unknown/custom agents land after
 * them, ordered by catalog sort_order and then id.
 */
const MODE_ORDER: Record<string, number> = {
  search: 0,
  fast: 1,
  team: 2,
};

export function isKnownAgentMode(id: string): id is AgentModeId {
  return (AGENT_MODE_IDS as readonly string[]).includes(id);
}

/**
 * Order agents for the mode switcher. Never filters: the caller already
 * receives only the modes this user is allowed to see from `/api/agents`.
 */
export function sortAgentModes<T extends Pick<AgentInfo, "id" | "sort_order">>(
  agents: readonly T[],
): T[] {
  return [...agents].sort((a, b) => {
    const knownRankA = MODE_ORDER[a.id];
    const knownRankB = MODE_ORDER[b.id];
    if (knownRankA !== undefined || knownRankB !== undefined) {
      if (knownRankA === undefined) return 1;
      if (knownRankB === undefined) return -1;
      return knownRankA - knownRankB;
    }

    const sortOrderA = a.sort_order ?? Number.MAX_SAFE_INTEGER;
    const sortOrderB = b.sort_order ?? Number.MAX_SAFE_INTEGER;
    if (sortOrderA !== sortOrderB) return sortOrderA - sortOrderB;
    return a.id.localeCompare(b.id);
  });
}
