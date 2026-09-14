// Personal storage management contracts. These mirror the additive storage API
// and intentionally keep response fields in snake_case like the backend.

export type StorageFileStatus =
  | "pending"
  | "active"
  | "delete_pending"
  | "deleted"
  | "migration_required"
  | "failed"
  | "missing";

export type StorageFileSource =
  | "chat"
  | "profile_avatar"
  | "persona_avatar"
  | "team_avatar"
  | "skill"
  | "wecom"
  | "legacy";

export type StorageUsageStatus = "normal" | "warning" | "full" | "over_quota";

export interface StorageUsageSummary {
  user_id?: string;
  used_bytes: number;
  pending_bytes?: number;
  quota_bytes: number;
  remaining_bytes: number;
  usage_percent: number;
  warning_percent?: number;
  warning_level?: string;
  status?: StorageUsageStatus;
  state?: string;
  enforcement_enabled?: boolean;
  source?: string;
  active_file_count?: number;
  over_quota?: boolean;
  generation?: string;
  version?: number;
  reconciled_at?: string | null;
  updated_at?: string;
}

export interface StorageFile {
  file_id: string;
  user_id?: string;
  blob_id?: string;
  source: StorageFileSource | string;
  source_ref?: string | null;
  name: string;
  mime_type: string;
  size: number;
  category?: string;
  content_hash?: string;
  status: StorageFileStatus | string;
  is_user_deletable: boolean;
  created_at: string;
  updated_at?: string;
  deleted_at?: string | null;
  deleted_reason?: string | null;
  quota_committed?: boolean;
  quota_released?: boolean;
  management_label?: string | null;
}

export interface StorageFileListParams {
  cursor?: string | null;
  limit?: number;
  source?: StorageFileSource | string;
  category?: string;
  q?: string;
  status?: StorageFileStatus | string;
  search?: string;
  sort?: "created_at" | "size" | "name";
  order?: "asc" | "desc";
}

export interface StorageFileListResponse {
  items: StorageFile[];
  next_cursor?: string | null;
  has_more?: boolean;
  total?: number;
  usage?: StorageUsageSummary;
}

export interface StorageFileStatusRequest {
  file_ids?: string[];
  keys?: string[];
}

export interface StorageFileLifecycleProjection {
  file_id?: string;
  key?: string;
  status: StorageFileStatus | string;
  available?: boolean;
  deleted_at?: string | null;
  source?: StorageFileSource | string;
}

export interface StorageDeleteResult {
  file_id: string;
  status: "deleted" | "delete_pending" | "preserved" | "failed" | string;
  logical_status?: string;
  released_bytes: number;
  physical_cleanup?: "scheduled" | "completed" | "preserved" | "failed" | string;
  physical_status?: string;
  message?: string;
}

export interface StorageDeleteResponse extends StorageDeleteResult {
  usage?: StorageUsageSummary;
}

export interface StorageBatchDeleteResponse {
  results: StorageDeleteResult[];
  usage?: StorageUsageSummary;
  deleted_count?: number;
  failed_count?: number;
  released_bytes?: number;
}

export interface StorageErrorDetail {
  code?: string;
  error?: string;
  message?: string;
  usage?: StorageUsageSummary;
  file_id?: string;
}

export interface StorageQuotaUpdate {
  quota_mb: number | null;
}

export function isProtectedStorageFile(file: StorageFile): boolean {
  return (
    file.is_user_deletable === false ||
    file.source === "profile_avatar" ||
    file.source === "persona_avatar" ||
    file.source === "team_avatar" ||
    file.source === "skill"
  );
}

export function getStorageUsageStatus(
  summary: Pick<StorageUsageSummary, "used_bytes" | "quota_bytes"> &
    Partial<Pick<StorageUsageSummary, "warning_percent" | "status">>,
): StorageUsageStatus {
  if (summary.status) return summary.status;
  if (summary.quota_bytes <= 0 || summary.used_bytes >= summary.quota_bytes) {
    return "full";
  }
  if (
    summary.used_bytes * 100 >=
    summary.quota_bytes * (summary.warning_percent || 80)
  ) {
    return "warning";
  }
  return "normal";
}

export function isStorageQuotaError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const candidate = error as {
    code?: unknown;
    status?: unknown;
    detail?: unknown;
  };
  if (
    candidate.code === "storage_quota_exceeded" ||
    candidate.code === "storage_operation_too_large"
  ) {
    return true;
  }
  if (candidate.status !== 413) return false;
  if (!candidate.detail || typeof candidate.detail !== "object") return true;
  const detail = candidate.detail as StorageErrorDetail;
  return (
    detail.code === "storage_quota_exceeded" ||
    detail.error === "storage_quota_exceeded" ||
    detail.code === "storage_operation_too_large" ||
    detail.error === "storage_operation_too_large"
  );
}

export function getStorageErrorDetail(error: unknown): StorageErrorDetail | null {
  if (!error || typeof error !== "object") return null;
  const candidate = error as { detail?: unknown; code?: unknown; message?: unknown };
  const raw = candidate.detail;
  if (raw && typeof raw === "object") {
    const detail = raw as StorageErrorDetail;
    return {
      ...detail,
      code:
        typeof detail.code === "string"
          ? detail.code
          : typeof detail.error === "string"
            ? detail.error
            : typeof candidate.code === "string"
              ? candidate.code
              : undefined,
    };
  }
  if (typeof candidate.code === "string") {
    return {
      code: candidate.code,
      message: typeof candidate.message === "string" ? candidate.message : undefined,
    };
  }
  return null;
}

export function isDeletedAttachmentStatus(
  attachment: {
    lifecycleStatus?: string;
    status?: string;
    deleted?: boolean;
    available?: boolean;
  },
): boolean {
  const explicitStatus = attachment.lifecycleStatus || attachment.status;
  return (
    attachment.deleted === true ||
    attachment.lifecycleStatus === "deleted" ||
    attachment.lifecycleStatus === "delete_pending" ||
    attachment.status === "deleted" ||
    attachment.status === "delete_pending" ||
    (!explicitStatus && attachment.available === false)
  );
}

export function formatStorageBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}
