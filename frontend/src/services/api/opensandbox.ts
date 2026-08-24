import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import type { OpenSandboxInventoryResponse } from "../../types";

export const openSandboxApi = {
  async listSandboxes(params: { skip?: number; limit?: number; node_id?: string; state?: string; search?: string } = {}) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== "") query.set(key, String(value)); });
    return authFetch<OpenSandboxInventoryResponse>(`${API_BASE}/api/opensandbox/sandboxes?${query.toString()}`);
  },
  pause(nodeId: string, sandboxId: string) { return authFetch(`${API_BASE}/api/opensandbox/sandboxes/${encodeURIComponent(nodeId)}/${encodeURIComponent(sandboxId)}/pause`, { method: "POST" }); },
  resume(nodeId: string, sandboxId: string) { return authFetch(`${API_BASE}/api/opensandbox/sandboxes/${encodeURIComponent(nodeId)}/${encodeURIComponent(sandboxId)}/resume`, { method: "POST" }); },
  renew(nodeId: string, sandboxId: string) { return authFetch(`${API_BASE}/api/opensandbox/sandboxes/${encodeURIComponent(nodeId)}/${encodeURIComponent(sandboxId)}/renew`, { method: "POST" }); },
  terminate(nodeId: string, sandboxId: string, options?: { local_only?: boolean }) {
    const query = new URLSearchParams({ confirm: "true" });
    if (options?.local_only) query.set("local_only", "true");
    return authFetch(`${API_BASE}/api/opensandbox/sandboxes/${encodeURIComponent(nodeId)}/${encodeURIComponent(sandboxId)}?${query.toString()}`, { method: "DELETE" });
  },
};
