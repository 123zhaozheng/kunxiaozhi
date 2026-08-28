/**
 * Analytics query serialization
 *
 * Single serialization point for every analytics usage endpoint so that all
 * requests (summary / trend / insights / by-agent / by-persona / by-model /
 * by-user / export) always carry identical `start`/`end`/filter parameters.
 * `start`/`end` are pure `YYYY-MM-DD` dates; the backend owns day boundaries.
 */

import type { AnalyticsDate, UsageFilters } from "../../types/analytics";

export function appendParam(
  query: string,
  key: string,
  value: string | number,
): string {
  return `${query}&${key}=${encodeURIComponent(String(value))}`;
}

/**
 * Build the query string shared by all usage report endpoints.
 *
 * Order is fixed (start, end, persona_preset_id, agent_id, role_id, extras)
 * so any two calls with the same arguments produce byte-identical queries.
 */
export function buildUsageQuery(
  start: AnalyticsDate,
  end: AnalyticsDate,
  filters?: UsageFilters,
  extras?: Record<string, string | number | undefined>,
): string {
  let query = `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  if (filters?.personaPresetId) {
    query = appendParam(query, "persona_preset_id", filters.personaPresetId);
  }
  if (filters?.agentId) {
    query = appendParam(query, "agent_id", filters.agentId);
  }
  if (filters?.roleId) {
    query = appendParam(query, "role_id", filters.roleId);
  }
  if (extras) {
    for (const [key, value] of Object.entries(extras)) {
      if (value === undefined) continue;
      query = appendParam(query, key, value);
    }
  }
  return query;
}
