/**
 * Settings API - 系统设置
 */

import type {
  SettingItem,
  SettingsResponse,
  SettingResetResponse,
  WeComNetworkConfig,
  WeComNetworkConfigUpdate,
  WeComNetworkOperationResponse,
} from "../../types";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export const settingsApi = {
  /**
   * Get all settings grouped by category
   */
  async list(): Promise<SettingsResponse> {
    return authFetch<SettingsResponse>(`${API_BASE}/api/settings/`);
  },

  /**
   * Get single setting
   */
  async get(key: string): Promise<SettingItem> {
    return authFetch<SettingItem>(`${API_BASE}/api/settings/${key}`);
  },

  /**
   * Update a setting
   */
  async update(
    key: string,
    value: string | number | boolean | object,
  ): Promise<SettingItem> {
    return authFetch<SettingItem>(`${API_BASE}/api/settings/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
  },

  /**
   * Reset all settings to defaults
   */
  async resetAll(): Promise<SettingResetResponse> {
    return authFetch<SettingResetResponse>(`${API_BASE}/api/settings/reset`, {
      method: "POST",
    });
  },

  /**
   * Reset single setting to default
   */
  async reset(key: string): Promise<SettingResetResponse> {
    return authFetch<SettingResetResponse>(
      `${API_BASE}/api/settings/reset/${key}`,
      {
        method: "POST",
      },
    );
  },

  /**
   * List Dify knowledge bases (for the persona KB picker).
   * Only succeeds when the Dify KB feature is enabled and configured.
   */
  async listDifyKbDatasets(): Promise<DifyKbDataset[]> {
    const res = await authFetch<{ datasets: DifyKbDataset[] }>(
      `${API_BASE}/api/settings/dify-kb/datasets`,
    );
    return res.datasets ?? [];
  },

  async getWeComNetwork(): Promise<WeComNetworkConfig> {
    return authFetch<WeComNetworkConfig>(
      `${API_BASE}/api/settings/wecom-network`,
    );
  },

  async testWeComNetwork(
    config: WeComNetworkConfigUpdate,
  ): Promise<WeComNetworkOperationResponse> {
    return authFetch<WeComNetworkOperationResponse>(
      `${API_BASE}/api/settings/wecom-network/test`,
      {
        method: "POST",
        body: JSON.stringify(config),
      },
    );
  },

  async updateWeComNetwork(
    config: WeComNetworkConfigUpdate,
  ): Promise<WeComNetworkOperationResponse> {
    return authFetch<WeComNetworkOperationResponse>(
      `${API_BASE}/api/settings/wecom-network`,
      {
        method: "PUT",
        body: JSON.stringify(config),
      },
    );
  },
};

export interface DifyKbDataset {
  id: string;
  name: string;
  description?: string | null;
  document_count?: number | null;
}
