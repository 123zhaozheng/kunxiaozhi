import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface LoginActivity {
  idle_timeout_seconds: number;
  last_activity_at: string;
  idle_expires_at: string;
}

export const activityApi = {
  get(): Promise<LoginActivity> {
    return authFetch<LoginActivity>(`${API_BASE}/api/auth/activity`);
  },
  touch(): Promise<LoginActivity> {
    return authFetch<LoginActivity>(`${API_BASE}/api/auth/activity`, {
      method: "POST",
    });
  },
};
