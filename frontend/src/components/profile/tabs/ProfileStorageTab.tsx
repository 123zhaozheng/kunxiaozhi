import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  File,
  HardDrive,
  Image,
  LockKeyhole,
  RefreshCw,
  Search,
  Trash2,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { ConfirmDialog } from "../../common/ConfirmDialog";
import { LoadingSpinner } from "../../common/LoadingSpinner";
import {
  getStorageErrorMessage,
  storageApi,
} from "../../../services/api";
import {
  dispatchStorageLifecycleEvent,
  STORAGE_LIFECYCLE_EVENT,
  type StorageLifecycleEventDetail,
} from "../../../services/storageLifecycle";
import type {
  StorageDeleteResponse,
  StorageFile,
  StorageFileSource,
  StorageFileStatus,
  StorageUsageSummary,
} from "../../../types/storage";
import {
  formatStorageBytes,
  getStorageUsageStatus,
  isProtectedStorageFile,
} from "../../../types/storage";
import { formatFileSize } from "../../common/AttachmentCard";
import { formatDateTimeShort } from "../../../utils/datetime";

const PAGE_SIZE = 20;

type DeleteTarget =
  | { kind: "single"; files: StorageFile[] }
  | { kind: "batch"; files: StorageFile[] }
  | null;

function sourceIcon(source: string) {
  if (source.includes("avatar")) return Image;
  if (source === "skill") return Wrench;
  return File;
}

function sourceLabel(
  source: string,
  translate: (key: string, fallback: string) => string,
): string {
  return translate(`storage.sources.${source}`, source.replaceAll("_", " "));
}

function statusLabel(
  status: string,
  translate: (key: string, fallback: string) => string,
): string {
  return translate(`storage.fileStatuses.${status}`, status.replaceAll("_", " "));
}

function UsageSummary({ summary }: { summary: StorageUsageSummary }) {
  const { t } = useTranslation();
  const status = getStorageUsageStatus(summary);
  const percent = Math.min(Math.max(summary.usage_percent, 0), 100);
  const statusText =
    status === "normal"
      ? t("storage.status.normal", "Normal")
      : status === "warning"
        ? t("storage.status.warning", "Storage warning")
        : status === "over_quota"
          ? t("storage.status.overQuota", "Over quota")
          : t("storage.status.full", "Storage full");
  const statusClass =
    status === "normal"
      ? "text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/30"
      : status === "warning"
        ? "text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/30"
        : "text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/30";
  const barClass =
    status === "normal"
      ? "bg-emerald-500"
      : status === "warning"
        ? "bg-amber-500"
        : "bg-red-500";

  return (
    <section
      aria-labelledby="storage-summary-title"
      className="rounded-2xl border border-stone-200/70 bg-stone-50/70 p-4 dark:border-stone-700/60 dark:bg-stone-900/30"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <HardDrive className="shrink-0 text-amber-500" size={18} />
          <div className="min-w-0">
            <h3
              id="storage-summary-title"
              className="text-sm font-semibold text-stone-900 dark:text-stone-100"
            >
              {t("storage.summaryTitle", "Personal storage")}
            </h3>
            <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">
              {formatStorageBytes(summary.used_bytes)} / {formatStorageBytes(summary.quota_bytes)}
              {summary.active_file_count !== undefined && (
                <span className="ml-2">
                  · {t("storage.fileCount", { count: summary.active_file_count })}
                </span>
              )}
            </p>
          </div>
        </div>
        <span
          className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium ${statusClass}`}
        >
          {status === "normal" ? (
            <CheckCircle2 size={12} aria-hidden="true" />
          ) : (
            <TriangleAlert size={12} aria-hidden="true" />
          )}
          {statusText}
        </span>
      </div>

      <div className="mt-4">
        <div
          className="h-2 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-label={t("storage.usageProgress", "Storage usage")}
        >
          <div
            className={`h-full rounded-full transition-[width] duration-300 ${barClass}`}
            style={{ width: `${percent}%` }}
          />
        </div>
        <div className="mt-2 flex items-center justify-between gap-3 text-xs text-stone-500 dark:text-stone-400">
          <span>{percent.toFixed(1)}%</span>
          <span>
            {formatStorageBytes(Math.max(summary.remaining_bytes, 0))} {t("storage.remaining", "remaining")}
          </span>
        </div>
      </div>

      {status !== "normal" && (
        <div
          className={`mt-3 flex items-start gap-2 rounded-xl px-3 py-2 text-xs ${statusClass}`}
          role="status"
        >
          <CircleHelp size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            {status === "warning"
              ? t("storage.warningHint", "You are using most of your storage. Remove unused files before uploading more.")
              : t("storage.fullHint", "New uploads are blocked until you free storage space.")}
          </span>
        </div>
      )}
    </section>
  );
}

export function ProfileStorageTab() {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<StorageUsageSummary | null>(null);
  const [files, setFiles] = useState<StorageFile[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [previousCursors, setPreviousCursors] = useState<string[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [sourceFilter, setSourceFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget>(null);
  const [partialResults, setPartialResults] = useState<StorageDeleteResponse[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPage = useCallback(
    async (pageCursor: string | null) => {
      setLoading(true);
      setError(null);
      try {
        const [usage, page] = await Promise.all([
          storageApi.getUsage(),
          storageApi.listFiles({
            cursor: pageCursor,
            limit: PAGE_SIZE,
            source: sourceFilter || undefined,
            status: statusFilter || undefined,
            search: search.trim() || undefined,
            sort: "created_at",
            order: "desc",
          }),
        ]);
        setSummary(usage);
        setFiles(page.items);
        setNextCursor(page.next_cursor ?? null);
        setHasMore(page.has_more ?? Boolean(page.next_cursor));
        setCursor(pageCursor);
        setSelectedIds(new Set());
      } catch (cause) {
        setError(getStorageErrorMessage(cause, t("storage.loadFailed", "Unable to load storage.")));
      } finally {
        setLoading(false);
      }
    },
    [search, sourceFilter, statusFilter, t],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPreviousCursors([]);
      void loadPage(null);
    }, search ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [loadPage, search, sourceFilter, statusFilter]);

  useEffect(() => {
    const refreshFromLifecycle = () => {
      void loadPage(null);
    };
    window.addEventListener(STORAGE_LIFECYCLE_EVENT, refreshFromLifecycle);
    return () =>
      window.removeEventListener(STORAGE_LIFECYCLE_EVENT, refreshFromLifecycle);
  }, [loadPage]);

  const reload = useCallback(() => loadPage(cursor), [cursor, loadPage]);

  const deletableFiles = useMemo(
    () => files.filter((file) => !isProtectedStorageFile(file) && file.status === "active"),
    [files],
  );
  const allDeletableSelected =
    deletableFiles.length > 0 && deletableFiles.every((file) => selectedIds.has(file.file_id));

  const toggleFile = (file: StorageFile) => {
    if (isProtectedStorageFile(file) || file.status !== "active") return;
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(file.file_id)) next.delete(file.file_id);
      else next.add(file.file_id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelectedIds(
      allDeletableSelected
        ? new Set()
        : new Set(deletableFiles.map((file) => file.file_id)),
    );
  };

  const openBatchDelete = () => {
    const selected = files.filter((file) => selectedIds.has(file.file_id));
    if (selected.length > 0) setDeleteTarget({ kind: "batch", files: selected });
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setPartialResults(null);
    try {
      if (deleteTarget.kind === "single") {
        const result = await storageApi.deleteFile(deleteTarget.files[0].file_id);
        setPartialResults([result]);
        dispatchStorageLifecycleEvent({
          fileIds: [result.file_id],
          status: result.status,
          usage: result.usage,
        });
        toast.success(t("storage.deleteComplete", "File removed from your storage."));
      } else {
        const result = await storageApi.deleteFiles(
          deleteTarget.files.map((file) => file.file_id),
        );
        const normalizedResults = result.results as StorageDeleteResponse[];
        setPartialResults(normalizedResults);
        const lifecycle: StorageLifecycleEventDetail = {
          fileIds: normalizedResults
            .filter(
              (item) =>
                item.status === "deleted" || item.status === "delete_pending",
            )
            .map((item) => item.file_id)
            .filter(Boolean),
          status: "deleted",
          usage: result.usage,
        };
        dispatchStorageLifecycleEvent(lifecycle);
        const failed = normalizedResults.filter(
          (item) => item.status === "failed" || item.status === "preserved",
        );
        toast[failed.length > 0 ? "error" : "success"](
          failed.length > 0
            ? t("storage.partialDelete", "Some files could not be removed.")
            : t("storage.deleteComplete", "Files removed from your storage."),
        );
      }
      setDeleteTarget(null);
      await reload();
    } catch (cause) {
      toast.error(getStorageErrorMessage(cause, t("storage.deleteFailed", "Unable to remove file.")));
    } finally {
      setDeleting(false);
    }
  };

  const translate = (key: string, fallback: string) => t(key, fallback);
  const dialogCount = deleteTarget?.files.length ?? 0;

  return (
    <div className="space-y-4">
      <ConfirmDialog
        isOpen={deleteTarget !== null}
        title={
          deleteTarget?.kind === "batch"
            ? t("storage.confirmBatchTitle", "Remove selected files?")
            : t("storage.confirmDeleteTitle", "Remove this file?")
        }
        message={
          deleteTarget?.kind === "batch"
            ? t("storage.confirmBatchMessage", { count: dialogCount })
            : t("storage.confirmDeleteMessage", "This file will no longer be available in your conversations. This cannot be undone.")
        }
        confirmText={t("common.delete", "Delete")}
        cancelText={t("common.cancel", "Cancel")}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        loading={deleting}
        variant="danger"
      />

      {summary && <UsageSummary summary={summary} />}

      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
            {t("storage.filesTitle", "Your files")}
          </h3>
          <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">
            {t("storage.filesHint", "Chat files can be removed here. Avatars and Skills stay protected.")}
          </p>
        </div>
        <button
          type="button"
          onClick={reload}
          disabled={loading}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-stone-200 px-2.5 py-1.5 text-xs font-medium text-stone-600 hover:bg-stone-50 disabled:opacity-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
          aria-label={t("common.refresh", "Refresh")}
        >
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          <span className="hidden sm:inline">{t("common.refresh", "Refresh")}</span>
        </button>
      </div>

      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto_auto]">
        <label className="relative block">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-stone-400" />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("storage.searchPlaceholder", "Search files")}
            className="w-full rounded-xl border border-stone-200 bg-white py-2 pl-9 pr-3 text-sm text-stone-800 outline-none focus:border-amber-400 dark:border-stone-700 dark:bg-stone-800 dark:text-stone-100"
          />
        </label>
        <select
          value={sourceFilter}
          onChange={(event) => setSourceFilter(event.target.value)}
          className="rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 dark:border-stone-700 dark:bg-stone-800 dark:text-stone-200"
          aria-label={t("storage.sourceFilter", "Filter by source")}
        >
          <option value="">{t("common.all", "All")}</option>
          <option value="chat">{sourceLabel("chat", translate)}</option>
          <option value="wecom">{sourceLabel("wecom", translate)}</option>
          <option value="profile_avatar">{sourceLabel("profile_avatar", translate)}</option>
          <option value="skill">{sourceLabel("skill", translate)}</option>
        </select>
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          className="rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 dark:border-stone-700 dark:bg-stone-800 dark:text-stone-200"
          aria-label={t("storage.statusFilter", "Filter by status")}
        >
          <option value="">{t("common.all", "All")}</option>
          <option value="active">{statusLabel("active", translate)}</option>
          <option value="deleted">{statusLabel("deleted", translate)}</option>
          <option value="delete_pending">{statusLabel("delete_pending", translate)}</option>
        </select>
      </div>

      {selectedIds.size > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2 dark:border-red-900/60 dark:bg-red-950/20">
          <span className="text-xs font-medium text-red-700 dark:text-red-300">
            {t("storage.selectedCount", { count: selectedIds.size })}
          </span>
          <button
            type="button"
            onClick={openBatchDelete}
            className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-red-700"
          >
            <Trash2 size={13} />
            {t("common.delete", "Delete")}
          </button>
        </div>
      )}

      {partialResults && partialResults.length > 0 && (
        <div
          role="status"
          className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-200"
        >
          <p>
            {partialResults.filter((item) => item.status === "failed" || item.status === "preserved").length > 0
              ? t("storage.partialDetails", "Some files remain protected or need another cleanup attempt.")
              : t("storage.releaseDetails", "Logical deletion completed; physical cleanup is tracked by the server.")}
          </p>
          <div className="mt-1 space-y-0.5 text-[11px]">
            {partialResults.map((item) => (
              <p key={item.file_id}>
                {statusLabel(item.status, translate)} · {t("storage.resultReleased", "Released: {{bytes}}", { bytes: formatStorageBytes(item.released_bytes) })} · {t("storage.resultPhysical", "Physical cleanup: {{status}}", { status: item.physical_cleanup ?? "unknown" })}
              </p>
            ))}
          </div>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/20 dark:text-red-300" role="alert">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1">{error}</span>
          <button type="button" onClick={reload} className="shrink-0 underline">
            {t("common.retry", "Retry")}
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12" role="status">
          <LoadingSpinner size="md" />
        </div>
      ) : files.length === 0 && !error ? (
        <div className="rounded-2xl border border-dashed border-stone-200 px-4 py-12 text-center dark:border-stone-700">
          <HardDrive size={24} className="mx-auto text-stone-400" />
          <p className="mt-2 text-sm font-medium text-stone-700 dark:text-stone-200">
            {t("storage.emptyTitle", "No managed files")}
          </p>
          <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">
            {t("storage.emptyHint", "Files you upload will appear here.")}
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-stone-200/70 dark:border-stone-700/60">
          <div className="flex items-center gap-3 border-b border-stone-200/70 bg-stone-50/70 px-3 py-2 dark:border-stone-700/60 dark:bg-stone-900/30">
            <input
              type="checkbox"
              checked={allDeletableSelected}
              onChange={toggleAll}
              disabled={deletableFiles.length === 0}
              aria-label={t("storage.selectAll", "Select all removable files")}
              className="size-4 accent-red-600"
            />
            <span className="text-xs font-medium text-stone-500 dark:text-stone-400">
              {t("storage.fileCount", { count: files.length })}
            </span>
          </div>
          <ul className="divide-y divide-stone-100 dark:divide-stone-700/60">
            {files.map((file) => {
              const protectedFile = isProtectedStorageFile(file);
              const Icon = sourceIcon(file.source);
              const isSelected = selectedIds.has(file.file_id);
              return (
                <li key={file.file_id} className="flex items-center gap-3 px-3 py-3 sm:px-4">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleFile(file)}
                    disabled={protectedFile || file.status !== "active"}
                    aria-label={t("storage.selectFile", { name: file.name })}
                    className="size-4 shrink-0 accent-red-600 disabled:opacity-30"
                  />
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400">
                    <Icon size={17} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-stone-800 dark:text-stone-100">
                      {file.name}
                    </p>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-stone-500 dark:text-stone-400">
                      <span>{sourceLabel(file.source, translate)}</span>
                      <span>{file.mime_type || file.category}</span>
                      <span>{formatFileSize(file.size)}</span>
                      {file.created_at && <span>{formatDateTimeShort(file.created_at)}</span>}
                      <span>{statusLabel(file.status, translate)}</span>
                    </div>
                  </div>
                  {protectedFile ? (
                    <span
                      className="inline-flex shrink-0 items-center gap-1 rounded-full bg-stone-100 px-2 py-1 text-[11px] text-stone-500 dark:bg-stone-800 dark:text-stone-400"
                      title={t("storage.protectedHint", "Manage this file from its owning feature.")}
                    >
                      <LockKeyhole size={12} />
                      <span className="hidden sm:inline">{t("storage.protected", "Protected")}</span>
                    </span>
                  ) : file.status === "active" ? (
                    <button
                      type="button"
                      onClick={() => setDeleteTarget({ kind: "single", files: [file] })}
                      className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-stone-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/30 dark:hover:text-red-400"
                      aria-label={t("storage.deleteFile", { name: file.name })}
                    >
                      <Trash2 size={15} />
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {(hasMore || previousCursors.length > 0) && (
            <div className="flex items-center justify-between border-t border-stone-200/70 px-3 py-2 dark:border-stone-700/60">
              <button
                type="button"
                disabled={previousCursors.length === 0 || loading}
                onClick={() => {
                  const history = [...previousCursors];
                  const previous = history.pop() ?? null;
                  setPreviousCursors(history);
                  void loadPage(previous);
                }}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-stone-600 hover:bg-stone-100 disabled:opacity-40 dark:text-stone-300 dark:hover:bg-stone-800"
              >
                <ChevronLeft size={14} /> {t("common.previous", "Previous")}
              </button>
              <span className="text-[11px] text-stone-400">
                {t("storage.page", "Page")}
              </span>
              <button
                type="button"
                disabled={!hasMore || !nextCursor || loading}
                onClick={() => {
                  if (!nextCursor) return;
                  setPreviousCursors((history) => [...history, cursor ?? ""]);
                  void loadPage(nextCursor);
                }}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-stone-600 hover:bg-stone-100 disabled:opacity-40 dark:text-stone-300 dark:hover:bg-stone-800"
              >
                {t("common.next", "Next")} <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export type { StorageFileSource, StorageFileStatus };
