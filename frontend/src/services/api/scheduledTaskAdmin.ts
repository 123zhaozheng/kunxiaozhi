/**
 * Scheduled Task Admin API - 定时任务管理（管理员）
 */

import { authFetch } from "./fetch";
import { API_BASE } from "./config";
import type {
  ScheduledTaskAdminItem,
  ScheduledTaskAdminListParams,
  ScheduledTaskAdminListResponse,
  TaskRunListResponse,
} from "../../types/scheduledTask";

const BASE = `${API_BASE}/api/admin/scheduled-tasks`;

/**
 * 把筛选参数拼成 query string（不含前导 "?"）。
 * 只拼接非空参数：undefined / null / 空白字符串会被忽略；
 * 数字 0（如 skip=0）是有效值，会保留。
 */
export function buildAdminListQuery(
  params: ScheduledTaskAdminListParams = {},
): string {
  const search = new URLSearchParams();
  const entries: [string, string | number | null | undefined][] = [
    ["skip", params.skip],
    ["limit", params.limit],
    ["q", params.q],
    ["owner", params.owner],
    ["status", params.status],
    ["last_run_status", params.last_run_status],
  ];
  for (const [key, value] of entries) {
    if (value === undefined || value === null) continue;
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (!trimmed) continue;
      search.set(key, trimmed);
    } else if (Number.isFinite(value)) {
      search.set(key, String(value));
    }
  }
  return search.toString();
}

export const scheduledTaskAdminApi = {
  list(
    params: ScheduledTaskAdminListParams = {},
  ): Promise<ScheduledTaskAdminListResponse> {
    const query = buildAdminListQuery(params);
    return authFetch<ScheduledTaskAdminListResponse>(
      query ? `${BASE}?${query}` : BASE,
    );
  },
  pause(id: string, reason: string): Promise<ScheduledTaskAdminItem> {
    return authFetch<ScheduledTaskAdminItem>(
      `${BASE}/${encodeURIComponent(id)}/pause`,
      { method: "POST", body: JSON.stringify({ reason }) },
    );
  },
  resume(id: string): Promise<ScheduledTaskAdminItem> {
    return authFetch<ScheduledTaskAdminItem>(
      `${BASE}/${encodeURIComponent(id)}/resume`,
      { method: "POST" },
    );
  },
  remove(id: string): Promise<void> {
    return authFetch<void>(`${BASE}/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },
  runs(id: string, skip = 0, limit = 20): Promise<TaskRunListResponse> {
    return authFetch<TaskRunListResponse>(
      `${BASE}/${encodeURIComponent(id)}/runs?skip=${skip}&limit=${limit}`,
    );
  },
};
