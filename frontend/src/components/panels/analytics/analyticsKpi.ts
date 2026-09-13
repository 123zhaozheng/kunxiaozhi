/**
 * Analytics KPI derivation — pure functions shared by the KPI row and the
 * donut row.
 *
 * Consistency contract (PRD R3.1): every donut center number is read from
 * the exact same accessor as its KPI card:
 *   - agent donut center   == sessions KPI card  == kpiSessionsValue
 *   - persona donut center == sessions KPI card  == kpiSessionsValue
 *   - model donut center   == total tokens card  == kpiTotalTokensValue
 *
 * The sessions KPI is the active-session count. The card renders
 * `new_sessions` as its secondary line; donut centers use this same accessor.
 */

import type { UsageSummaryResponse } from "../../../types/analytics";

/** First KPI card switches label/value when a persona/agent filter is active. */
export function kpiUsersValue(
  summary: UsageSummaryResponse | null,
  isFiltered: boolean,
): number | null {
  if (!summary) return null;
  return isFiltered ? summary.using_users : summary.active_users;
}

/** Sessions KPI card == both session donut centers (active-session count). */
export function kpiSessionsValue(summary: UsageSummaryResponse | null): number | null {
  return summary ? summary.active_sessions : null;
}

export function kpiUserMessagesValue(
  summary: UsageSummaryResponse | null,
): number | null {
  return summary ? summary.user_messages : null;
}

/** Total tokens KPI card == model-token donut center. */
export function kpiTotalTokensValue(
  summary: UsageSummaryResponse | null,
): number | null {
  return summary ? summary.total_tokens : null;
}

/** 人均消息 = user_messages ÷ using_users (denominator: users that messaged). */
export function messagesPerUser(
  summary: UsageSummaryResponse | null,
): number | null {
  if (!summary || summary.using_users <= 0) return null;
  return summary.user_messages / summary.using_users;
}

/** 会话均 Token = total_tokens ÷ active_sessions. */
export function tokensPerSession(
  summary: UsageSummaryResponse | null,
): number | null {
  if (!summary || summary.active_sessions <= 0) return null;
  return summary.total_tokens / summary.active_sessions;
}

/**
 * Percentage change vs the previous equal-length period.
 * Returns null (render nothing, never "0%") when `previous` is missing or
 * the base is zero / non-finite.
 */
export function deltaPct(current: number, previous: number | null | undefined): number | null {
  if (previous === null || previous === undefined) return null;
  if (!Number.isFinite(previous) || !Number.isFinite(current)) return null;
  if (previous === 0) return null;
  return ((current - previous) / previous) * 100;
}

export type KpiMetric =
  | "users"
  | "sessions"
  | "userMessages"
  | "totalTokens"
  | "messagesPerUser"
  | "tokensPerSession";

/**
 * Previous-period counterpart of each KPI metric (derived metrics included).
 * Null when `summary.previous` is unavailable or the denominator is zero.
 */
export function previousMetricValue(
  summary: UsageSummaryResponse | null,
  metric: KpiMetric,
  isFiltered: boolean,
): number | null {
  const previous = summary?.previous ?? null;
  if (!previous) return null;
  switch (metric) {
    case "users":
      return isFiltered ? previous.using_users : previous.active_users;
    case "sessions":
      return previous.active_sessions;
    case "userMessages":
      return previous.user_messages;
    case "totalTokens":
      return previous.total_tokens;
    case "messagesPerUser":
      return previous.using_users > 0
        ? previous.user_messages / previous.using_users
        : null;
    case "tokensPerSession":
      return previous.active_sessions > 0
        ? previous.total_tokens / previous.active_sessions
        : null;
  }
}

/** Δ% of a KPI metric vs the previous period; null hides the badge. */
export function kpiDeltaPct(
  summary: UsageSummaryResponse | null,
  metric: KpiMetric,
  isFiltered: boolean,
): number | null {
  if (!summary) return null;
  const current =
    metric === "users"
      ? kpiUsersValue(summary, isFiltered)
      : metric === "sessions"
        ? kpiSessionsValue(summary)
        : metric === "userMessages"
          ? kpiUserMessagesValue(summary)
          : metric === "totalTokens"
            ? kpiTotalTokensValue(summary)
            : metric === "messagesPerUser"
              ? messagesPerUser(summary)
              : tokensPerSession(summary);
  if (current === null) return null;
  return deltaPct(current, previousMetricValue(summary, metric, isFiltered));
}

/** Formats a ratio such as 人均消息 / 会话均 Token; null → "—" (never NaN). */
export function formatRatio(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded)
    ? rounded.toLocaleString()
    : rounded.toLocaleString(undefined, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      });
}

/** Signed delta display: "+12.3%" / "-5.0%"; null is never rendered. */
export function formatDeltaPct(pct: number): string {
  const rounded = Math.round(pct * 10) / 10;
  return `${rounded >= 0 ? "+" : "-"}${Math.abs(rounded).toFixed(1)}%`;
}
