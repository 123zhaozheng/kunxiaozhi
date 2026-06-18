/**
 * Analytics API - 全局统计看板数据接口
 */

import { authFetch } from "./fetch";
import { API_BASE } from "./config";
import type {
  ByLabelResponse,
  ByPresetFeedbackResponse,
  FeedbackListResponse,
  FeedbackSummaryResponse,
  HeatmapResponse,
  OverviewResponse,
  PresetAnalyticsResponse,
  RunListResponse,
  SessionListResponse,
  SessionsTrendResponse,
  TrendResponse,
} from "../../types/analytics";

const BASE = `${API_BASE}/api/analytics`;

function rangeQuery(start: string, end: string): string {
  return `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
}

function appendParam(query: string, key: string, value: string | number): string {
  return `${query}&${key}=${encodeURIComponent(String(value))}`;
}

export const analyticsApi = {
  async getOverview(start: string, end: string): Promise<OverviewResponse> {
    return authFetch<OverviewResponse>(`${BASE}/overview${rangeQuery(start, end)}`);
  },

  async getActiveUserTrend(
    start: string,
    end: string,
  ): Promise<TrendResponse> {
    return authFetch<TrendResponse>(
      `${BASE}/users/active${rangeQuery(start, end)}`,
    );
  },

  async getUsersHeatmap(
    start: string,
    end: string,
  ): Promise<HeatmapResponse> {
    return authFetch<HeatmapResponse>(
      `${BASE}/users/heatmap${rangeQuery(start, end)}`,
    );
  },

  async getSessionsTrend(
    start: string,
    end: string,
  ): Promise<SessionsTrendResponse> {
    return authFetch<SessionsTrendResponse>(
      `${BASE}/sessions/trend${rangeQuery(start, end)}`,
    );
  },

  async getTokensByModel(
    start: string,
    end: string,
  ): Promise<ByLabelResponse> {
    return authFetch<ByLabelResponse>(
      `${BASE}/tokens/by-model${rangeQuery(start, end)}`,
    );
  },

  async getTokensByPreset(
    start: string,
    end: string,
    limit: number = 10,
  ): Promise<ByLabelResponse> {
    return authFetch<ByLabelResponse>(
      `${BASE}/tokens/by-preset${rangeQuery(start, end)}&limit=${limit}`,
    );
  },

  async getTokensTrend(start: string, end: string): Promise<TrendResponse> {
    return authFetch<TrendResponse>(
      `${BASE}/tokens/trend${rangeQuery(start, end)}`,
    );
  },

  // ── PR2: 单角色智能体 + 反馈 + 钻取明细 ─────────────────────────────

  async getPresetAnalytics(
    presetId: string,
    start: string,
    end: string,
  ): Promise<PresetAnalyticsResponse> {
    return authFetch<PresetAnalyticsResponse>(
      `${BASE}/presets/${encodeURIComponent(presetId)}${rangeQuery(start, end)}`,
    );
  },

  async getFeedbackSummary(
    start: string,
    end: string,
  ): Promise<FeedbackSummaryResponse> {
    return authFetch<FeedbackSummaryResponse>(
      `${BASE}/feedback/summary${rangeQuery(start, end)}`,
    );
  },

  async getFeedbackByPreset(
    start: string,
    end: string,
  ): Promise<ByPresetFeedbackResponse> {
    return authFetch<ByPresetFeedbackResponse>(
      `${BASE}/feedback/by-preset${rangeQuery(start, end)}`,
    );
  },

  async listSessions(
    start: string,
    end: string,
    options?: { presetId?: string; skip?: number; limit?: number },
  ): Promise<SessionListResponse> {
    let query = rangeQuery(start, end);
    if (options?.presetId) {
      query = appendParam(query, "preset_id", options.presetId);
    }
    query = appendParam(query, "skip", options?.skip ?? 0);
    query = appendParam(query, "limit", options?.limit ?? 20);
    return authFetch<SessionListResponse>(`${BASE}/sessions/list${query}`);
  },

  async listFeedback(
    start: string,
    end: string,
    options?: {
      presetId?: string;
      rating?: "up" | "down";
      skip?: number;
      limit?: number;
    },
  ): Promise<FeedbackListResponse> {
    let query = rangeQuery(start, end);
    if (options?.presetId) {
      query = appendParam(query, "preset_id", options.presetId);
    }
    if (options?.rating) {
      query = appendParam(query, "rating", options.rating);
    }
    query = appendParam(query, "skip", options?.skip ?? 0);
    query = appendParam(query, "limit", options?.limit ?? 20);
    return authFetch<FeedbackListResponse>(`${BASE}/feedback/list${query}`);
  },

  async listRuns(
    start: string,
    end: string,
    options?: { presetId?: string; skip?: number; limit?: number },
  ): Promise<RunListResponse> {
    let query = rangeQuery(start, end);
    if (options?.presetId) {
      query = appendParam(query, "preset_id", options.presetId);
    }
    query = appendParam(query, "skip", options?.skip ?? 0);
    query = appendParam(query, "limit", options?.limit ?? 20);
    return authFetch<RunListResponse>(`${BASE}/runs/list${query}`);
  },
};
