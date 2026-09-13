/**
 * Analytics Type Definitions
 *
 * Mirrors backend Pydantic schemas in `src/kernel/schemas/analytics.py`.
 * Field casing follows API (snake_case) rather than TS convention.
 */

export interface OverviewResponse {
  active_users: number;
  total_sessions: number;
  total_tokens: number;
  up_vote_rate: number;
}

export interface TrendDataPoint {
  date: string;
  value: number;
}

export interface TrendResponse {
  items: TrendDataPoint[];
}

export interface ByLabelItem {
  label: string;
  value: number;
  /** Optional stable id (e.g. persona_preset_id) for drilldown filters */
  id?: string | null;
}

export interface ByLabelResponse {
  items: ByLabelItem[];
}

export interface SessionsTrendResponse {
  sessions: TrendDataPoint[];
  messages: TrendDataPoint[];
  total_sessions: number;
}

export type AnalyticsRangePreset = "1d" | "7d" | "30d" | "custom";

/**
 * Pure calendar date string `YYYY-MM-DD` (e.g. `2026-08-22`).
 *
 * Every analytics endpoint takes this shape for `start`/`end`; the backend
 * expands it into a UTC+8 half-open interval. Never an ISO timestamp
 * (no `T` / `Z`), and day boundaries are owned by the backend only.
 */
export type AnalyticsDate = string;

// ── PR2: 单角色智能体 + 反馈 + 钻取明细 ─────────────────────────────

export interface PresetAnalyticsResponse {
  total_messages: number;
  total_sessions: number;
  active_users: number;
  total_tokens: number;
  up_vote_rate: number;
  down_reasons: ByLabelItem[];
}

export interface FeedbackSummaryResponse {
  total: number;
  up_count: number;
  down_count: number;
  up_percentage: number;
  reason_distribution: ByLabelItem[];
}

export interface ByPresetFeedbackItem {
  preset_id: string;
  preset_name: string;
  up_count: number;
  down_count: number;
  total: number;
  up_percentage: number;
}

export interface ByPresetFeedbackResponse {
  items: ByPresetFeedbackItem[];
}

export interface SessionListItem {
  id: string;
  name: string | null;
  user_id: string | null;
  /** Username (employee id) when users collection join succeeds */
  username?: string | null;
  agent_id: string;
  created_at: string;
  updated_at: string;
  is_active: boolean;
  task_status: string | null;
  unread_count: number;
  persona_preset_id: string | null;
  persona_preset_name: string | null;
}

export interface FeedbackListItem {
  id: string;
  user_id: string;
  username: string;
  session_id: string;
  run_id: string;
  rating: string;
  comment: string | null;
  reason: string | null;
  created_at: string;
  persona_preset_id: string | null;
  persona_preset_name: string | null;
}

export interface RunListItem {
  run_id: string;
  trace_id: string | null;
  session_id: string;
  agent_id: string;
  user_id: string | null;
  started_at: string;
  completed_at: string | null;
  status: string;
  event_count: number;
  total_tokens: number;
  persona_preset_id: string | null;
}

export interface AnalyticsListResponse<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
  has_more: boolean;
}

export type SessionListResponse = AnalyticsListResponse<SessionListItem>;
export type FeedbackListResponse = AnalyticsListResponse<FeedbackListItem>;
export type RunListResponse = AnalyticsListResponse<RunListItem>;

/** Active-user drilldown item (admin analytics). */
export interface ActiveUserListItem {
  user_id: string;
  username: string;
  display_name: string | null;
  roles: string[];
  session_count: number;
  last_active_at: string | null;
}

export type ActiveUserListResponse = AnalyticsListResponse<ActiveUserListItem>;

/** Shared list/export query filters for sessions + active users. */
export type AnalyticsListSort = "recent" | "frequency";

export interface AnalyticsListFilters {
  agentId?: string;
  personaPresetId?: string;
  /** RBAC user role id */
  roleId?: string;
  /** Optional first-use filter; older API versions safely ignore it. */
  firstUse?: boolean;
  sort?: AnalyticsListSort;
  skip?: number;
  limit?: number;
}

// ── 使用情况报表（统一口径）─────────────────────────────────────────
// Mirrors src/kernel/schemas/analytics.py Usage* models.
// 「用户消息」= user:message 事件条数；「活跃」= 区间内发过消息。

/** Filters shared by every usage report endpoint. */
export interface UsageFilters {
  personaPresetId?: string;
  agentId?: string;
  /** RBAC user role id */
  roleId?: string;
}

/** Previous equal-length period values for KPI ±% deltas (same fields, no `previous`). */
export interface UsageSummaryPrevious {
  active_users: number;
  using_users: number;
  new_sessions: number;
  active_sessions: number;
  user_messages: number;
  total_tokens: number;
}

export interface UsageSummaryResponse {
  /** Unfiltered: distinct logged-in users; filtered: equals using_users */
  active_users: number;
  /** Distinct users that sent at least one message in range */
  using_users: number;
  new_sessions: number;
  active_sessions: number;
  user_messages: number;
  total_tokens: number;
  /** Immediately preceding equal-length period (same filters); null when unavailable */
  previous: UsageSummaryPrevious | null;
}

/** Peak activity bucket (UTC+8) from user message times. */
export interface UsageInsightsPeak {
  /** 0=Sunday ... 6=Saturday */
  weekday: number;
  /** 0-23 */
  hour: number;
  user_messages: number;
}

export interface UsageInsightsTopTokenUser {
  user_id: string;
  username: string;
  display_name: string | null;
  tokens: number;
}

export interface UsageInsightsFastestGrowingPersona {
  persona_preset_id: string;
  persona_preset_name: string;
  current: number;
  previous: number;
  /** Percentage growth vs previous period (can be negative) */
  growth_pct: number;
}

/** The four insight-bar conclusions in one request (null / [] / 0 when data is insufficient). */
export interface UsageInsightsResponse {
  peak: UsageInsightsPeak | null;
  top_token_users: UsageInsightsTopTokenUser[];
  fastest_growing_persona: UsageInsightsFastestGrowingPersona | null;
  new_users: number;
}

export interface UsageTrendPoint {
  /** YYYY-MM-DD in Asia/Shanghai */
  date: string;
  new_sessions: number;
  active_sessions: number;
  user_messages: number;
  total_tokens: number;
}

export interface UsageTrendResponse {
  items: UsageTrendPoint[];
}

/** One row per user × persona. */
export interface UsageByUserItem {
  user_id: string;
  username: string;
  display_name: string | null;
  roles: string[];
  persona_preset_id: string | null;
  persona_preset_name: string;
  new_sessions: number;
  active_sessions: number;
  user_messages: number;
  total_tokens: number;
  last_active_at: string | null;
}

export type UsageByUserResponse = AnalyticsListResponse<UsageByUserItem>;
