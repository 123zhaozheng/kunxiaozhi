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
