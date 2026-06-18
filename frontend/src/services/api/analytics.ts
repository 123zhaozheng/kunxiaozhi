/**
 * Analytics API - 全局统计看板数据接口
 */

import { authFetch } from "./fetch";
import { API_BASE } from "./config";
import type {
  ByLabelResponse,
  HeatmapResponse,
  OverviewResponse,
  SessionsTrendResponse,
  TrendResponse,
} from "../../types/analytics";

const BASE = `${API_BASE}/api/analytics`;

function rangeQuery(start: string, end: string): string {
  return `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
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
};
