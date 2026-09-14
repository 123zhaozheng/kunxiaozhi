import type { StorageUsageSummary } from "../types/storage";

export const STORAGE_LIFECYCLE_EVENT = "storage:file-lifecycle";
export const STORAGE_OPEN_MANAGEMENT_EVENT = "storage:open-management";

export interface StorageLifecycleEventDetail {
  fileIds?: string[];
  keys?: string[];
  status?: string;
  usage?: StorageUsageSummary;
}

export function dispatchStorageLifecycleEvent(
  detail: StorageLifecycleEventDetail,
): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<StorageLifecycleEventDetail>(STORAGE_LIFECYCLE_EVENT, {
      detail,
    }),
  );
}

export function requestStorageManagement(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(STORAGE_OPEN_MANAGEMENT_EVENT));
}

export function matchesStorageLifecycleEvent(
  detail: StorageLifecycleEventDetail | undefined,
  fileId?: string,
  key?: string,
): boolean {
  if (!detail) return false;
  if (fileId && detail.fileIds?.includes(fileId)) return true;
  if (key && detail.keys?.includes(key)) return true;
  return false;
}
