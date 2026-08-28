/**
 * Analytics API - 全局统计看板数据接口
 */

import { authenticatedRequest } from "./authenticatedRequest";
import { authFetch } from "./fetch";
import { API_BASE } from "./config";
import type {
  ActiveUserListResponse,
  AnalyticsListFilters,
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
  UsageByPersonaResponse,
  UsageByUserResponse,
  UsageFilters,
  UsageSummaryResponse,
  UsageTrendResponse,
} from "../../types/analytics";

const BASE = `${API_BASE}/api/analytics`;

function rangeQuery(start: string, end: string): string {
  return `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
}

function appendParam(query: string, key: string, value: string | number): string {
  return `${query}&${key}=${encodeURIComponent(String(value))}`;
}

/** Shared filter query for every usage report endpoint (summary/trend/by-persona/by-user/export). */
function appendUsageFilters(query: string, filters?: UsageFilters): string {
  let q = query;
  if (filters?.personaPresetId) {
    q = appendParam(q, "persona_preset_id", filters.personaPresetId);
  }
  if (filters?.agentId) {
    q = appendParam(q, "agent_id", filters.agentId);
  }
  if (filters?.roleId) {
    q = appendParam(q, "role_id", filters.roleId);
  }
  return q;
}

/** Shared filter query for list + CSV export. */
function appendListFilters(
  query: string,
  options?: AnalyticsListFilters,
  opts?: {
    defaultSort?: string;
    includePagination?: boolean;
  },
): string {
  let q = query;
  const includePagination = opts?.includePagination ?? true;
  const personaId = options?.personaPresetId;
  if (personaId) {
    q = appendParam(q, "persona_preset_id", personaId);
  }
  if (options?.agentId) {
    q = appendParam(q, "agent_id", options.agentId);
  }
  if (options?.roleId) {
    q = appendParam(q, "role_id", options.roleId);
  }
  if (options?.sort || opts?.defaultSort) {
    q = appendParam(q, "sort", options?.sort ?? opts!.defaultSort!);
  }
  if (includePagination) {
    q = appendParam(q, "skip", options?.skip ?? 0);
    q = appendParam(q, "limit", options?.limit ?? 20);
  }
  return q;
}

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function downloadCsv(url: string, fallbackFilename: string): Promise<void> {
  const response = await authenticatedRequest(url, {
    headers: { Accept: "text/csv" },
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const detail =
      typeof (errorData as { detail?: unknown })?.detail === "string"
        ? (errorData as { detail: string }).detail
        : `Export failed: ${response.statusText}`;
    throw new Error(detail);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const filename = match?.[1] || fallbackFilename;
  triggerBrowserDownload(blob, filename);
}

export const analyticsApi = {
  async getOverview(start: string, end: string): Promise<OverviewResponse> {
    return authFetch<OverviewResponse>(`${BASE}/overview${rangeQuery(start, end)}`);
  },

  async getActiveUserTrend(
    start: string,
    end: string,
    filters?: UsageFilters,
  ): Promise<TrendResponse> {
    const query = appendUsageFilters(rangeQuery(start, end), filters);
    return authFetch<TrendResponse>(`${BASE}/users/active${query}`);
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

  async getSessionsByAgent(
    start: string,
    end: string,
    limit: number = 10,
  ): Promise<ByLabelResponse> {
    return authFetch<ByLabelResponse>(
      `${BASE}/sessions/by-agent${rangeQuery(start, end)}&limit=${limit}`,
    );
  },

  async getSessionsByPersona(
    start: string,
    end: string,
    limit: number = 10,
  ): Promise<ByLabelResponse> {
    return authFetch<ByLabelResponse>(
      `${BASE}/sessions/by-persona${rangeQuery(start, end)}&limit=${limit}`,
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
    options?: AnalyticsListFilters,
  ): Promise<SessionListResponse> {
    const query = appendListFilters(rangeQuery(start, end), options, {
      includePagination: true,
    });
    return authFetch<SessionListResponse>(`${BASE}/sessions/list${query}`);
  },

  async listActiveUsers(
    start: string,
    end: string,
    options?: AnalyticsListFilters,
  ): Promise<ActiveUserListResponse> {
    const query = appendListFilters(rangeQuery(start, end), options, {
      defaultSort: "frequency",
      includePagination: true,
    });
    return authFetch<ActiveUserListResponse>(`${BASE}/users/list${query}`);
  },

  /**
   * Download sessions CSV for the current drilldown filters (full filtered set, server-capped).
   * Same filter/sort params as listSessions (no skip/limit).
   */
  async exportSessionsCsv(
    start: string,
    end: string,
    options?: AnalyticsListFilters,
  ): Promise<void> {
    const query = appendListFilters(rangeQuery(start, end), options, {
      includePagination: false,
    });
    await downloadCsv(
      `${BASE}/sessions/export.csv${query}`,
      "analytics-sessions.csv",
    );
  },

  /**
   * Download active users CSV for the current drilldown filters (full filtered set, server-capped).
   * Same filter/sort params as listActiveUsers (no skip/limit).
   */
  async exportUsersCsv(
    start: string,
    end: string,
    options?: AnalyticsListFilters,
  ): Promise<void> {
    const query = appendListFilters(rangeQuery(start, end), options, {
      defaultSort: "frequency",
      includePagination: false,
    });
    await downloadCsv(
      `${BASE}/users/export.csv${query}`,
      "analytics-users.csv",
    );
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

  // ── 使用情况报表（统一口径）─────────────────────────────────────────
  // 五个出口共用同一套筛选参数，后端由同一查询构造层产出，数字天然一致。

  async getUsageSummary(
    start: string,
    end: string,
    filters?: UsageFilters,
  ): Promise<UsageSummaryResponse> {
    const query = appendUsageFilters(rangeQuery(start, end), filters);
    return authFetch<UsageSummaryResponse>(`${BASE}/usage/summary${query}`);
  },

  async getUsageTrend(
    start: string,
    end: string,
    filters?: UsageFilters,
  ): Promise<UsageTrendResponse> {
    const query = appendUsageFilters(rangeQuery(start, end), filters);
    return authFetch<UsageTrendResponse>(`${BASE}/usage/trend${query}`);
  },

  async getUsageByPersona(
    start: string,
    end: string,
    filters?: UsageFilters,
  ): Promise<UsageByPersonaResponse> {
    const query = appendUsageFilters(rangeQuery(start, end), filters);
    return authFetch<UsageByPersonaResponse>(
      `${BASE}/usage/by-persona${query}`,
    );
  },

  /** One row per user × persona. */
  async listUsageByUser(
    start: string,
    end: string,
    filters?: UsageFilters,
    pagination?: { skip?: number; limit?: number },
  ): Promise<UsageByUserResponse> {
    let query = appendUsageFilters(rangeQuery(start, end), filters);
    query = appendParam(query, "skip", pagination?.skip ?? 0);
    query = appendParam(query, "limit", pagination?.limit ?? 20);
    return authFetch<UsageByUserResponse>(`${BASE}/usage/by-user${query}`);
  },

  /**
   * Download the user × persona usage CSV for the current filters
   * (full filtered set, server-capped; no skip/limit).
   */
  async exportUsageCsv(
    start: string,
    end: string,
    filters?: UsageFilters,
  ): Promise<void> {
    const query = appendUsageFilters(rangeQuery(start, end), filters);
    await downloadCsv(`${BASE}/usage/export.csv${query}`, "analytics-usage.csv");
  },
};
