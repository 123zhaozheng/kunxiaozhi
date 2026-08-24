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
  OpenSandboxNodeInput,
  OpenSandboxNodesResponse,
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

  async getOpenSandboxNodes(): Promise<OpenSandboxNodesResponse> {
    return authFetch<OpenSandboxNodesResponse>(`${API_BASE}/api/settings/opensandbox-nodes`);
  },

  async updateOpenSandboxNodes(
    mode: "legacy" | "multi_node",
    nodes: OpenSandboxNodeInput[],
    expected_revision?: string,
  ): Promise<OpenSandboxNodesResponse> {
    return authFetch<OpenSandboxNodesResponse>(`${API_BASE}/api/settings/opensandbox-nodes`, {
      method: "PUT",
      body: JSON.stringify({ mode, nodes, expected_revision }),
    });
  },

  async probeOpenSandboxNode(nodeId: string) {
    return authFetch<{ node_id: string; health_state: string; latency_ms?: number; detail?: string | null }>(
      `${API_BASE}/api/settings/opensandbox-nodes/${encodeURIComponent(nodeId)}/probe`,
      { method: "POST" },
    );
  },

  async drainOpenSandboxNode(nodeId: string, draining: boolean) {
    return authFetch<{ node_id: string; draining: boolean }>(
      `${API_BASE}/api/settings/opensandbox-nodes/${encodeURIComponent(nodeId)}/drain?draining=${draining ? "true" : "false"}`,
      { method: "POST" },
    );
  },

  async forceRemoveOpenSandboxNode(
    nodeId: string,
    body: { confirm: boolean; expected_revision: string },
  ): Promise<OpenSandboxNodesResponse> {
    return authFetch<OpenSandboxNodesResponse>(
      `${API_BASE}/api/settings/opensandbox-nodes/${encodeURIComponent(nodeId)}/force-remove`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },
};

export interface DifyKbDataset {
  id: string;
  name: string;
  description?: string | null;
  document_count?: number | null;
}
