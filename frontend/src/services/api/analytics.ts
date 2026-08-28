/**
 * Analytics API - 全局统计看板数据接口
 */

import { authenticatedRequest } from "./authenticatedRequest";
import { authFetch } from "./fetch";
import { API_BASE } from "./config";
import { appendParam, buildUsageQuery } from "./analyticsQuery";
import type {
  ActiveUserListResponse,
  AnalyticsDate,
  AnalyticsListFilters,
  ByLabelResponse,
  ByPresetFeedbackResponse,
  FeedbackListResponse,
  FeedbackSummaryResponse,
  PresetAnalyticsResponse,
  RunListResponse,
  SessionListResponse,
  TrendResponse,
  UsageByUserResponse,
  UsageFilters,
  UsageInsightsResponse,
  UsageSummaryResponse,
  UsageTrendResponse,
} from "../../types/analytics";

const BASE = `${API_BASE}/api/analytics`;

function rangeQuery(start: string, end: string): string {
  return `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
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
  async getActiveUserTrend(
    start: AnalyticsDate,
    end: AnalyticsDate,
    filters?: UsageFilters,
  ): Promise<TrendResponse> {
    return authFetch<TrendResponse>(
      `${BASE}/users/active${buildUsageQuery(start, end, filters)}`,
    );
  },

  async getTokensByModel(
    start: AnalyticsDate,
    end: AnalyticsDate,
    filters?: UsageFilters,
  ): Promise<ByLabelResponse> {
    return authFetch<ByLabelResponse>(
      `${BASE}/tokens/by-model${buildUsageQuery(start, end, filters)}`,
    );
  },

  async getSessionsByAgent(
    start: AnalyticsDate,
    end: AnalyticsDate,
    filters?: UsageFilters,
    limit: number = 10,
  ): Promise<ByLabelResponse> {
    return authFetch<ByLabelResponse>(
      `${BASE}/sessions/by-agent${buildUsageQuery(start, end, filters, { limit })}`,
    );
  },

  async getSessionsByPersona(
    start: AnalyticsDate,
    end: AnalyticsDate,
    filters?: UsageFilters,
    limit: number = 10,
  ): Promise<ByLabelResponse> {
    return authFetch<ByLabelResponse>(
      `${BASE}/sessions/by-persona${buildUsageQuery(start, end, filters, { limit })}`,
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
  // 八个出口（summary / trend / insights / by-agent / by-persona / by-model /
  // by-user / export）共用 buildUsageQuery 序列化筛选参数，保证任意筛选变化
  // 时所有请求收到完全一致的 start / end / persona_preset_id / agent_id。

  async getUsageSummary(
    start: AnalyticsDate,
    end: AnalyticsDate,
    filters?: UsageFilters,
  ): Promise<UsageSummaryResponse> {
    return authFetch<UsageSummaryResponse>(
      `${BASE}/usage/summary${buildUsageQuery(start, end, filters)}`,
    );
  },

  async getUsageTrend(
    start: AnalyticsDate,
    end: AnalyticsDate,
    filters?: UsageFilters,
  ): Promise<UsageTrendResponse> {
    return authFetch<UsageTrendResponse>(
      `${BASE}/usage/trend${buildUsageQuery(start, end, filters)}`,
    );
  },

  /** 洞察栏四条结论一次给全（峰值 / Top3 token 用户 / 增长最快 Persona / 新增用户）。 */
  async getUsageInsights(
    start: AnalyticsDate,
    end: AnalyticsDate,
    filters?: UsageFilters,
  ): Promise<UsageInsightsResponse> {
    return authFetch<UsageInsightsResponse>(
      `${BASE}/usage/insights${buildUsageQuery(start, end, filters)}`,
    );
  },

  /** One row per user × persona. */
  async listUsageByUser(
    start: AnalyticsDate,
    end: AnalyticsDate,
    filters?: UsageFilters,
    pagination?: { skip?: number; limit?: number },
  ): Promise<UsageByUserResponse> {
    const query = buildUsageQuery(start, end, filters, {
      skip: pagination?.skip ?? 0,
      limit: pagination?.limit ?? 20,
    });
    return authFetch<UsageByUserResponse>(`${BASE}/usage/by-user${query}`);
  },

  /**
   * Download the user × persona usage CSV for the current filters
   * (full filtered set, server-capped; no skip/limit).
   */
  async exportUsageCsv(
    start: AnalyticsDate,
    end: AnalyticsDate,
    filters?: UsageFilters,
  ): Promise<void> {
    await downloadCsv(
      `${BASE}/usage/export.csv${buildUsageQuery(start, end, filters)}`,
      "analytics-usage.csv",
    );
  },
};
