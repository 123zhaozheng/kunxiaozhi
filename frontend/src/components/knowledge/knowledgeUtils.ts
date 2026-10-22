import type { TFunction } from "i18next";
import type {
  KBChunkPreset,
  KBConfig,
  KBDocument,
  KBPublicStatus,
  KBQuota,
  KBRole,
  KBDocumentStatus,
  KBTask,
  KnowledgeBase,
} from "../../services/api/knowledgeBase";

export const POLL_BASE_MS = 5000;
export const POLL_MAX_MS = 30000;
export const POLL_BACKOFF_FACTOR = 1.5;
export const POST_ACTION_REFRESH_MS = 1000;
export const UPLOAD_CONCURRENCY = 3;
export const DOCUMENT_PAGE_SIZE = 20;

export const CHUNK_TOKEN_MIN = 100;
export const CHUNK_TOKEN_MAX = 2048;
export const CHUNK_TOKEN_DEFAULT = 512;
export const OVERLAP_MIN = 0;
export const OVERLAP_MAX = 50;

export const CHUNK_PRESET_KEYS: KBChunkPreset[] = [
  "general",
  "qa",
  "book",
  "laws",
];

export const DOCUMENT_STATUSES: KBDocumentStatus[] = [
  "uploaded",
  "parsing",
  "parsed",
  "indexing",
  "indexed",
  "error_parsing",
  "error_indexing",
];

export type StatusTone = "neutral" | "info" | "warning" | "success" | "danger";

export interface DocStatusMeta {
  tone: StatusTone;
  /** Still moving through the pipeline (spinner shown). */
  processing: boolean;
  failed: boolean;
}

const STATUS_META: Record<KBDocumentStatus, DocStatusMeta> = {
  uploaded: { tone: "neutral", processing: true, failed: false },
  parsing: { tone: "info", processing: true, failed: false },
  parsed: { tone: "warning", processing: true, failed: false },
  indexing: { tone: "info", processing: true, failed: false },
  indexed: { tone: "success", processing: false, failed: false },
  error_parsing: { tone: "danger", processing: false, failed: true },
  error_indexing: { tone: "danger", processing: false, failed: true },
};

export function getDocStatusMeta(status: KBDocumentStatus): DocStatusMeta {
  return STATUS_META[status] ?? STATUS_META.uploaded;
}

const STATUS_DEFAULT_LABELS: Record<KBDocumentStatus, string> = {
  uploaded: "排队中",
  parsing: "解析中",
  parsed: "待入库",
  indexing: "入库中",
  indexed: "已完成",
  error_parsing: "解析失败",
  error_indexing: "入库失败",
};

export function getDocStatusLabel(t: TFunction, status: KBDocumentStatus): string {
  return t(`knowledge.status.${status}`, STATUS_DEFAULT_LABELS[status]);
}

export const STATUS_TONE_CLASSES: Record<StatusTone, string> = {
  neutral:
    "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300",
  info: "bg-sky-50 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  warning:
    "bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  success:
    "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  danger: "bg-red-50 text-red-700 dark:bg-red-900/40 dark:text-red-300",
};

export function isProcessingStatus(status: KBDocumentStatus): boolean {
  return getDocStatusMeta(status).processing;
}

export function isFailedStatus(status: KBDocumentStatus): boolean {
  return getDocStatusMeta(status).failed;
}

/** The backend may send `error` as an object or (legacy) a plain string. */
export function getDocErrorMessage(doc: Pick<KBDocument, "error">): string {
  const { error } = doc;
  if (!error) return "";
  if (typeof error === "string") return error;
  return error.message ?? "";
}

export function hasActiveTask(task: KBTask | null | undefined): boolean {
  return !!task && (task.status === "queued" || task.status === "running");
}

export function isKbProcessing(
  kb: Pick<KnowledgeBase, "stats" | "active_task"> | null,
  docs: Array<Pick<KBDocument, "status">> = [],
): boolean {
  if (!kb) return false;
  return (
    kb.stats.processing_count > 0 ||
    hasActiveTask(kb.active_task) ||
    docs.some((doc) => isProcessingStatus(doc.status))
  );
}

/** Compact fingerprint used to tell whether a poll returned anything new. */
export function buildPollSignature(
  kb: Pick<KnowledgeBase, "stats" | "active_task" | "index_outdated"> | null,
  docs: Array<Pick<KBDocument, "id" | "status" | "chunk_count">>,
): string {
  const task = kb?.active_task;
  const parts = [
    kb
      ? [
          kb.stats.doc_count,
          kb.stats.indexed_count,
          kb.stats.processing_count,
          kb.stats.failed_count,
          kb.stats.chunk_count,
          kb.index_outdated ? 1 : 0,
        ].join(",")
      : "-",
    task
      ? [
          task.id,
          task.status,
          task.progress.done,
          task.progress.failed,
          task.progress.total,
          task.progress.current_doc_id ?? "",
        ].join(",")
      : "-",
    ...docs.map((d) => `${d.id}:${d.status}:${d.chunk_count}`),
  ];
  return parts.join("|");
}

/** Next delay for the chained poll: reset on change, otherwise back off up to the cap. */
export function nextPollDelay(
  currentMs: number,
  changed: boolean,
  baseMs: number = POLL_BASE_MS,
  maxMs: number = POLL_MAX_MS,
  factor: number = POLL_BACKOFF_FACTOR,
): number {
  if (changed) return baseMs;
  return Math.min(maxMs, Math.round(currentMs * factor));
}

export function taskProgressPercent(task: KBTask | null | undefined): number {
  if (!task || task.progress.total <= 0) return 0;
  const finished = task.progress.done + task.progress.failed;
  return Math.max(0, Math.min(100, Math.round((finished / task.progress.total) * 100)));
}

export function getFileExtension(filename: string): string {
  const idx = filename.lastIndexOf(".");
  if (idx <= 0 || idx === filename.length - 1) return "";
  return filename.slice(idx).toLowerCase();
}

export type UploadValidationError =
  | { code: "extension"; ext: string }
  | { code: "empty" }
  | { code: "size"; maxMb: number };

/** Mirrors the backend whitelist and `max_file_mb` so obvious rejects never hit the wire. */
export function validateUploadFile(
  file: Pick<File, "name" | "size">,
  config: Pick<KBConfig, "allowed_extensions" | "max_file_mb"> | null,
): UploadValidationError | null {
  const ext = getFileExtension(file.name);
  const allowed = (config?.allowed_extensions ?? []).map((e) => e.toLowerCase());
  if (allowed.length > 0 && !allowed.includes(ext)) {
    return { code: "extension", ext: ext || file.name };
  }
  if (file.size <= 0) return { code: "empty" };
  const maxMb = config?.max_file_mb ?? 0;
  if (maxMb > 0 && file.size > maxMb * 1024 * 1024) {
    return { code: "size", maxMb };
  }
  return null;
}

export function formatLineRange(start: number, end: number): string {
  return start === end ? `${start}` : `${start}-${end}`;
}

export function formatScorePercent(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return "–";
  return `${Math.round(Math.max(0, Math.min(1, score)) * 100)}%`;
}

export function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, Math.round(value)));
}

export function parseBoundedInt(
  raw: string,
  min: number,
  max: number,
): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return value >= min && value <= max ? value : null;
}

export function getDefaultChunkSettings(
  config: Pick<KBConfig, "presets"> | null,
  preset: KBChunkPreset,
): { chunk_token_num: number; overlap_percent: number } {
  const found = config?.presets.find((p) => p.key === preset);
  return {
    chunk_token_num: found?.chunk_token_num ?? CHUNK_TOKEN_DEFAULT,
    overlap_percent: found?.overlap_percent ?? 0,
  };
}

/** Drops ids that are no longer on the visible page. */
export function pruneSelection(
  selected: ReadonlySet<string>,
  docs: Array<Pick<KBDocument, "id">>,
): Set<string> {
  const visible = new Set(docs.map((d) => d.id));
  return new Set([...selected].filter((id) => visible.has(id)));
}

export type KbDetailTab = "documents" | "search" | "settings";

export const KB_DETAIL_TABS: KbDetailTab[] = ["documents", "search", "settings"];

export function resolveKbDetailTab(value: string | null | undefined): KbDetailTab {
  return KB_DETAIL_TABS.includes(value as KbDetailTab)
    ? (value as KbDetailTab)
    : "documents";
}

export const SEARCH_TOP_K_DEFAULT = 10;
export const SEARCH_TOP_K_MAX = 50;

/** Items to start now so that at most `concurrency` uploads run at once. */
export function pickPendingToStart<T extends { status: string }>(
  items: readonly T[],
  concurrency: number = UPLOAD_CONCURRENCY,
): T[] {
  const running = items.filter((i) => i.status === "uploading").length;
  const slots = Math.max(0, concurrency - running);
  return items.filter((i) => i.status === "pending").slice(0, slots);
}

/* ── Roles & permissions ─────────────────────────────── */

export interface KbCapabilities {
  canWriteDocs: boolean;
  canManageSettings: boolean;
  canShare: boolean;
  canSubscribe: boolean;
  canUnsubscribe: boolean;
  canLeave: boolean;
}

/** Backend capability flags win over role-derived defaults. */
export function getKbCapabilities(
  role: KBRole | null | undefined,
  flags: Partial<Pick<KnowledgeBase, "can_write" | "can_manage">> = {},
): KbCapabilities {
  const r = role ?? "owner";
  const canManage = flags.can_manage ?? r === "owner";
  return {
    canWriteDocs: flags.can_write ?? (r === "owner" || r === "editor"),
    canManageSettings: canManage,
    canShare: canManage,
    canSubscribe: r === "visitor",
    canUnsubscribe: r === "subscriber",
    canLeave: r === "editor" || r === "viewer",
  };
}

/** Public publishing is allowed unless the backend explicitly reports it as disabled. */
export function isPublicAllowed(config: Partial<Pick<KBConfig, "allow_public">> | null): boolean {
  return config?.allow_public !== false;
}

export type KbPublicTone = StatusTone;

export function getPublicStatusMeta(
  status: KBPublicStatus | undefined,
): { tone: KbPublicTone; visible: boolean } {
  switch (status) {
    case "approved":
      return { tone: "success", visible: true };
    case "pending":
      return { tone: "warning", visible: true };
    case "rejected":
    case "delisted":
      return { tone: "danger", visible: true };
    default:
      return { tone: "neutral", visible: false };
  }
}

/* ── Quota ───────────────────────────────────────────── */

export function isKbCountQuotaReached(
  quota: Pick<KBQuota, "limits" | "usage"> | null,
): boolean {
  return !!quota && quota.limits.kb_max_count > 0 && quota.usage.kb_count >= quota.limits.kb_max_count;
}

export function quotaPercent(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((used / limit) * 100)));
}

/* ── Username paste parsing ──────────────────────────── */

export const SHARE_MAX_USERNAMES = 50;

export interface ParsedUsernames {
  usernames: string[];
  /** Entries dropped because the list already holds the maximum. */
  overflow: number;
}

/** Splits on commas (ASCII/full-width), whitespace and newlines; dedupes case-insensitively. */
export function parseUsernameInput(
  raw: string,
  existing: readonly string[] = [],
  max: number = SHARE_MAX_USERNAMES,
): ParsedUsernames {
  const seen = new Set(existing.map((u) => u.toLowerCase()));
  const usernames = [...existing];
  let overflow = 0;
  for (const part of raw.split(/[\s,，、;；]+/)) {
    const name = part.trim();
    if (!name) continue;
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    if (usernames.length >= max) {
      overflow += 1;
      continue;
    }
    seen.add(key);
    usernames.push(name);
  }
  return { usernames, overflow };
}

/* ── File library import ─────────────────────────────── */

export const IMPORT_MAX_FILES = 50;

/** Only non-project library files with a supported extension can be imported. */
export function canImportToKnowledgeBase(
  file: { file_type: string; file_name: string },
  allowedExtensions: readonly string[] | null | undefined,
): boolean {
  if (file.file_type === "project") return false;
  const ext = getFileExtension(file.file_name);
  if (!ext) return false;
  return (allowedExtensions ?? []).some((e) => e.toLowerCase() === ext);
}

/* ── List tabs ───────────────────────────────────────── */

export type KbListTab = "mine" | "shared" | "plaza";
export type KbSharedFilter = "all" | "shared" | "subscribed";

export const KB_LIST_TABS: KbListTab[] = ["mine", "shared", "plaza"];
export const KB_SHARED_FILTERS: KbSharedFilter[] = ["all", "shared", "subscribed"];

export function resolveKbListTab(value: string | null | undefined): KbListTab {
  return KB_LIST_TABS.includes(value as KbListTab) ? (value as KbListTab) : "mine";
}

export function resolveKbSharedFilter(value: string | null | undefined): KbSharedFilter {
  return KB_SHARED_FILTERS.includes(value as KbSharedFilter)
    ? (value as KbSharedFilter)
    : "all";
}

/** Merges lists by id, keeping the first occurrence. */
export function mergeKbLists<T extends { id: string }>(...lists: T[][]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const list of lists) {
    for (const item of list) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      out.push(item);
    }
  }
  return out;
}

export const KB_NOTIFICATION_POLL_MS = 60_000;
export const PLAZA_PAGE_SIZE = 12;
export const ADMIN_PAGE_SIZE = 20;
export const IMPORT_PAGE_SIZE = 10;
