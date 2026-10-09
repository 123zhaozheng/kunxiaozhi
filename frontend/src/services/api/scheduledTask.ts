/**
 * Scheduled Task API - 定时任务
 */

import { authFetch } from "./fetch";
import { API_BASE } from "./config";
import type {
  SchedulePreview,
  ScheduleSpec,
  ScheduledTask,
  ScheduledTaskDraft,
  ScheduledTaskDraftConfirmResponse,
  ScheduledTaskInput,
  ScheduledTaskListResponse,
  ScheduledTaskTemplate,
  TaskRunListResponse,
} from "../../types/scheduledTask";

const BASE = `${API_BASE}/api/scheduled-tasks`;

export const scheduledTaskApi = {
  list(): Promise<ScheduledTaskListResponse> {
    return authFetch<ScheduledTaskListResponse>(BASE);
  },
  templates(): Promise<ScheduledTaskTemplate[]> {
    return authFetch<ScheduledTaskTemplate[]>(`${BASE}/templates`);
  },
  preview(
    schedule: ScheduleSpec,
    timezone: string,
    count = 5,
  ): Promise<SchedulePreview> {
    return authFetch<SchedulePreview>(`${BASE}/preview`, {
      method: "POST",
      body: JSON.stringify({ schedule, timezone, count }),
    });
  },
  create(data: ScheduledTaskInput): Promise<ScheduledTask> {
    return authFetch<ScheduledTask>(BASE, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },
  update(id: string, data: Partial<ScheduledTaskInput>): Promise<ScheduledTask> {
    return authFetch<ScheduledTask>(`${BASE}/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },
  pause(id: string): Promise<ScheduledTask> {
    return authFetch<ScheduledTask>(`${BASE}/${id}/pause`, { method: "POST" });
  },
  resume(id: string): Promise<ScheduledTask> {
    return authFetch<ScheduledTask>(`${BASE}/${id}/resume`, { method: "POST" });
  },
  runNow(id: string): Promise<{ run_id: string; session_id: string }> {
    return authFetch<{ run_id: string; session_id: string }>(
      `${BASE}/${id}/run`,
      { method: "POST" },
    );
  },
  remove(id: string): Promise<void> {
    return authFetch<void>(`${BASE}/${id}`, { method: "DELETE" });
  },
  runs(id: string, skip = 0, limit = 20): Promise<TaskRunListResponse> {
    return authFetch<TaskRunListResponse>(
      `${BASE}/${id}/runs?skip=${skip}&limit=${limit}`,
    );
  },
  getDraft(id: string): Promise<ScheduledTaskDraft> {
    return authFetch<ScheduledTaskDraft>(
      `${BASE}/drafts/${encodeURIComponent(id)}`,
    );
  },
  confirmDraft(id: string): Promise<ScheduledTaskDraftConfirmResponse> {
    return authFetch<ScheduledTaskDraftConfirmResponse>(
      `${BASE}/drafts/${encodeURIComponent(id)}/confirm`,
      { method: "POST" },
    );
  },
  cancelDraft(id: string): Promise<ScheduledTaskDraft> {
    return authFetch<ScheduledTaskDraft>(
      `${BASE}/drafts/${encodeURIComponent(id)}/cancel`,
      { method: "POST" },
    );
  },
};
