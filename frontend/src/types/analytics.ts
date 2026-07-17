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

export interface HeatmapCell {
  weekday: number;
  hour: number;
  count: number;
}

export interface HeatmapResponse {
  cells: HeatmapCell[];
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
  sort?: AnalyticsListSort;
  skip?: number;
  limit?: number;
  /** @deprecated prefer personaPresetId */
  presetId?: string;
}
