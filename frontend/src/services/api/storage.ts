import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import type {
  StorageBatchDeleteResponse,
  StorageDeleteResponse,
  StorageErrorDetail,
  StorageFile,
  StorageFileLifecycleProjection,
  StorageFileListParams,
  StorageFileListResponse,
  StorageQuotaUpdate,
  StorageUsageSummary,
} from "../../types/storage";

const MAX_PAGE_SIZE = 100;

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function normalizeUsage(raw: unknown): StorageUsageSummary {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<
    string,
    unknown
  >;
  const used = asNumber(value.used_bytes ?? value.usedBytes);
  const quota = asNumber(value.quota_bytes ?? value.quotaBytes);
  const remaining = asNumber(
    value.remaining_bytes ?? value.remainingBytes,
    Math.max(quota - used, 0),
  );
  const warning = asNumber(
    value.warning_percent ?? value.warningPercent,
    80,
  );
  const percent = asNumber(
    value.usage_percent ?? value.usagePercent,
    quota > 0 ? (used / quota) * 100 : 0,
  );
  const status = value.status;
  const warningLevel = value.warning_level ?? value.warningLevel;
  const state = value.state;
  const warningStatus =
    warningLevel === "over_quota"
      ? "over_quota"
      : warningLevel === "full"
        ? "full"
        : warningLevel === "notice" || warningLevel === "critical"
          ? "warning"
          : undefined;
  const normalizedStatus =
    status === "warning" ||
    status === "full" ||
    status === "over_quota" ||
    status === "normal"
      ? status
      : state === "reconciliation_required"
        ? "over_quota"
        : warningStatus ||
            (quota > 0 && used >= quota
              ? "full"
              : percent >= warning
                ? "warning"
                : "normal");

  return {
    user_id: typeof value.user_id === "string" ? value.user_id : undefined,
    used_bytes: used,
    pending_bytes: asNumber(value.pending_bytes ?? value.pendingBytes),
    quota_bytes: quota,
    remaining_bytes: remaining,
    usage_percent: percent,
    warning_percent: warning,
    warning_level:
      typeof warningLevel === "string" ? warningLevel : undefined,
    status: normalizedStatus,
    state: typeof state === "string" ? state : undefined,
    enforcement_enabled:
      typeof value.enforcement_enabled === "boolean"
        ? value.enforcement_enabled
        : undefined,
    source: typeof value.source === "string" ? value.source : undefined,
    active_file_count: asNumber(
      value.active_file_count ?? value.activeFileCount,
    ),
    over_quota: value.over_quota === true || value.overQuota === true,
    generation: typeof value.generation === "string" ? value.generation : undefined,
    version: typeof value.version === "number" ? value.version : undefined,
    reconciled_at:
      typeof value.reconciled_at === "string" ? value.reconciled_at : null,
    updated_at:
      typeof value.updated_at === "string"
        ? value.updated_at
        : typeof value.updatedAt === "string"
          ? value.updatedAt
          : undefined,
  };
}

function normalizeStorageFile(raw: unknown): StorageFile {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<
    string,
    unknown
  >;
  return {
    file_id: String(value.file_id ?? value.fileId ?? value._id ?? ""),
    user_id:
      typeof value.user_id === "string" ? value.user_id : undefined,
    blob_id: typeof value.blob_id === "string" ? value.blob_id : undefined,
    source: String(value.source ?? "legacy"),
    source_ref:
      typeof value.source_ref === "string" ? value.source_ref : null,
    name: String(value.name ?? ""),
    mime_type: String(value.mime_type ?? value.mimeType ?? ""),
    size: asNumber(value.size),
    category: typeof value.category === "string" ? value.category : undefined,
    content_hash:
      typeof value.content_hash === "string" ? value.content_hash : undefined,
    status: String(value.status ?? "active"),
    is_user_deletable:
      value.is_user_deletable !== false && value.isUserDeletable !== false,
    created_at: String(value.created_at ?? value.createdAt ?? ""),
    updated_at:
      typeof value.updated_at === "string" ? value.updated_at : undefined,
    deleted_at:
      typeof value.deleted_at === "string" ? value.deleted_at : null,
    deleted_reason:
      typeof value.deleted_reason === "string" ? value.deleted_reason : null,
    quota_committed:
      typeof value.quota_committed === "boolean"
        ? value.quota_committed
        : undefined,
    quota_released:
      typeof value.quota_released === "boolean"
        ? value.quota_released
        : undefined,
    management_label:
      typeof value.management_label === "string"
        ? value.management_label
        : null,
  };
}

function normalizeList(raw: unknown): StorageFileListResponse {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<
    string,
    unknown
  >;
  const rawItems = Array.isArray(value.items)
    ? value.items
    : Array.isArray(value.files)
      ? value.files
      : [];
  return {
    items: rawItems.map(normalizeStorageFile),
    next_cursor:
      typeof value.next_cursor === "string"
        ? value.next_cursor
        : typeof value.nextCursor === "string"
          ? value.nextCursor
          : null,
    has_more:
      typeof value.has_more === "boolean"
        ? value.has_more
        : typeof value.hasMore === "boolean"
          ? value.hasMore
          : undefined,
    total: typeof value.total === "number" ? value.total : undefined,
    usage: value.usage ? normalizeUsage(value.usage) : undefined,
  };
}

function normalizeDeleteResponse(raw: unknown): StorageDeleteResponse {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<
    string,
    unknown
  >;
  return {
    file_id: String(value.file_id ?? value.fileId ?? ""),
    status: String(
      value.status ??
        (value.logical_status === "deleted"
          ? "deleted"
          : value.logical_status === "managed_by_source"
            ? "preserved"
            : value.logical_status ?? "deleted"),
    ),
    logical_status:
      typeof value.logical_status === "string" ? value.logical_status : undefined,
    released_bytes: asNumber(
      value.released_bytes ?? value.releasedBytes ?? value.bytes_released,
    ),
    physical_cleanup:
      typeof value.physical_cleanup === "string"
        ? value.physical_cleanup
        : typeof value.physicalCleanup === "string"
          ? value.physicalCleanup
          : typeof value.physical_status === "string"
            ? value.physical_status
          : undefined,
    physical_status:
      typeof value.physical_status === "string" ? value.physical_status : undefined,
    message: typeof value.message === "string" ? value.message : undefined,
    usage: value.usage ? normalizeUsage(value.usage) : undefined,
  };
}

function normalizeBatchResponse(raw: unknown): StorageBatchDeleteResponse {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<
    string,
    unknown
  >;
  const rawResults = Array.isArray(value.results) ? value.results : [];
  return {
    results: rawResults.map((result) => normalizeDeleteResponse(result)),
    usage: value.usage ? normalizeUsage(value.usage) : undefined,
    deleted_count:
      typeof value.deleted_count === "number" ? value.deleted_count : undefined,
    failed_count:
      typeof value.failed_count === "number"
        ? value.failed_count
        : typeof value.failed === "number"
          ? value.failed
          : undefined,
    released_bytes: asNumber(
      value.released_bytes ?? value.releasedBytes,
    ),
  };
}

export function buildStorageFilesUrl(params: StorageFileListParams = {}): string {
  const search = new URLSearchParams();
  if (params.cursor) search.set("cursor", params.cursor);
  if (params.limit !== undefined) {
    search.set(
      "limit",
      String(Math.min(Math.max(Math.floor(params.limit), 1), MAX_PAGE_SIZE)),
    );
  }
  if (params.source) search.set("source", params.source);
  if (params.category) search.set("category", params.category);
  if (params.status) search.set("status", params.status);
  if (params.search || params.q) search.set("q", params.search || params.q || "");
  if (params.sort) search.set("sort", params.sort);
  search.set("descending", String(params.order !== "asc"));
  const query = search.toString();
  return `${API_BASE}/api/storage/files${query ? `?${query}` : ""}`;
}

export function getStorageErrorCode(error: unknown): string | undefined {
  if (!error || typeof error !== "object") return undefined;
  const candidate = error as { code?: unknown; detail?: unknown };
  if (typeof candidate.code === "string") return candidate.code;
  const detail = candidate.detail;
  if (!detail || typeof detail !== "object") return undefined;
  const typed = detail as StorageErrorDetail;
  return typeof typed.code === "string"
    ? typed.code
    : typeof typed.error === "string"
      ? typed.error
      : undefined;
}

export function getStorageErrorMessage(
  error: unknown,
  fallback = "Storage operation failed",
): string {
  if (typeof error === "string") return error;
  if (!error || typeof error !== "object") return fallback;
  const candidate = error as { message?: unknown; detail?: unknown };
  if (candidate.detail && typeof candidate.detail === "object") {
    const detail = candidate.detail as StorageErrorDetail;
    if (typeof detail.message === "string") return detail.message;
  }
  if (typeof candidate.detail === "string") return candidate.detail;
  return typeof candidate.message === "string" ? candidate.message : fallback;
}

export function parseStorageError(error: unknown): StorageErrorDetail {
  const detail =
    error && typeof error === "object"
      ? (error as { detail?: unknown; code?: unknown; message?: unknown })
      : {};
  const raw = detail.detail;
  const objectDetail =
    raw && typeof raw === "object" ? (raw as StorageErrorDetail) : undefined;
  return {
    code:
      getStorageErrorCode(error) ??
      (typeof detail.code === "string" ? detail.code : undefined),
    message:
      getStorageErrorMessage(error, "") ||
      (typeof detail.message === "string" ? detail.message : undefined),
    usage: objectDetail?.usage,
  };
}

export const storageApi = {
  async getUsage(): Promise<StorageUsageSummary> {
    const result = await authFetch<unknown>(`${API_BASE}/api/storage/usage`);
    return normalizeUsage(result);
  },

  async listFiles(
    params: StorageFileListParams = {},
  ): Promise<StorageFileListResponse> {
    const result = await authFetch<unknown>(buildStorageFilesUrl(params));
    return normalizeList(result);
  },

  async getStatuses(
    fileIds: string[],
    keys: string[] = [],
  ): Promise<StorageFileLifecycleProjection[]> {
    const result = await authFetch<unknown>(`${API_BASE}/api/storage/files/status`, {
      method: "POST",
      body: JSON.stringify({ file_ids: fileIds, keys }),
    });
    const raw = Array.isArray(result)
      ? result
      : result && typeof result === "object" && "items" in result
        ? (result as { items?: unknown[] }).items ?? []
        : [];
    return raw.filter(
      (item): item is StorageFileLifecycleProjection =>
        !!item && typeof item === "object",
    );
  },

  async deleteFile(fileId: string): Promise<StorageDeleteResponse> {
    const result = await authFetch<unknown>(
      `${API_BASE}/api/storage/files/${encodeURIComponent(fileId)}`,
      { method: "DELETE" },
    );
    return normalizeDeleteResponse(result);
  },

  async deleteFiles(fileIds: string[]): Promise<StorageBatchDeleteResponse> {
    const result = await authFetch<unknown>(`${API_BASE}/api/storage/files/batch-delete`, {
      method: "POST",
      body: JSON.stringify({ file_ids: fileIds }),
    });
    return normalizeBatchResponse(result);
  },

  async setUserQuota(
    userId: string,
    quota: StorageQuotaUpdate,
  ): Promise<StorageUsageSummary> {
    const result = await authFetch<unknown>(
      `${API_BASE}/api/storage/admin/users/${encodeURIComponent(userId)}/quota`,
      {
        method: "PUT",
        body: JSON.stringify(quota),
      },
    );
    return normalizeUsage(
      result && typeof result === "object" && "usage" in result
        ? (result as { usage: unknown }).usage
        : result,
    );
  },

  // Friendly aliases keep the API seam stable for profile/admin callers.
  async getSummary(): Promise<StorageUsageSummary> {
    return this.getUsage();
  },
  async list(params: StorageFileListParams = {}): Promise<StorageFileListResponse> {
    return this.listFiles(params);
  },
  async removeFiles(fileIds: string[]): Promise<StorageBatchDeleteResponse> {
    return this.deleteFiles(fileIds);
  },
  async delete(fileId: string): Promise<StorageDeleteResponse> {
    return this.deleteFile(fileId);
  },
  async batchDelete(fileIds: string[]): Promise<StorageBatchDeleteResponse> {
    return this.deleteFiles(fileIds);
  },
};

export {
  normalizeUsage,
  normalizeStorageFile,
  normalizeList,
  normalizeDeleteResponse,
};
