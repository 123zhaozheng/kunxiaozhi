import { authFetch } from "./fetch";
import { API_BASE } from "./config";
import type {
  Notification,
  NotificationCreate,
  NotificationListResponse,
  NotificationUpdate,
} from "../../types/notification";

export const notificationApi = {
  async getActive(): Promise<Notification[]> {
    try {
      return await authFetch<Notification[]>(
        `${API_BASE}/api/notifications/active`,
      );
    } catch {
      return [];
    }
  },

  async getPopupEligible(): Promise<Notification[]> {
    try {
      return await authFetch<Notification[]>(
        `${API_BASE}/api/notifications/active?popup_eligible=true`,
      );
    } catch {
      return [];
    }
  },

  async list(
    skip: number = 0,
    limit: number = 50,
  ): Promise<NotificationListResponse> {
    const params = new URLSearchParams({
      skip: skip.toString(),
      limit: limit.toString(),
    });
    return authFetch<NotificationListResponse>(
      `${API_BASE}/api/notifications/admin?${params}`,
    );
  },

  async create(data: NotificationCreate): Promise<Notification> {
    return authFetch<Notification>(`${API_BASE}/api/notifications/`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  async update(id: string, data: NotificationUpdate): Promise<Notification> {
    return authFetch<Notification>(`${API_BASE}/api/notifications/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
  },

  async delete(id: string): Promise<void> {
    return authFetch(`${API_BASE}/api/notifications/${id}`, {
      method: "DELETE",
    });
  },

  async dismiss(id: string, snoozeUntil?: string): Promise<void> {
    return authFetch(`${API_BASE}/api/notifications/${id}/dismiss`, {
      method: "POST",
      ...(snoozeUntil
        ? { body: JSON.stringify({ snooze_until: snoozeUntil }) }
        : {}),
    });
  },
};
