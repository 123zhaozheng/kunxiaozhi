/**
 * Knowledge base API - 个人知识库
 * Types mirror the backend `/api/kb` contract (snake_case).
 */

import i18n from "i18next";
import { API_BASE } from "./config";
import { ApiRequestError, authFetch } from "./fetch";
import { authenticatedRequest } from "./authenticatedRequest";
import { getRefreshToken } from "./token";
import {
  getValidAccessToken,
  redirectToLogin,
  refreshAccessToken,
} from "./tokenManager";

const BASE = `${API_BASE}/api/kb`;

export type KBChunkPreset = "general" | "qa" | "book" | "laws";

export type KBDocumentStatus =
  | "uploaded"
  | "parsing"
  | "parsed"
  | "indexing"
  | "indexed"
  | "error_parsing"
  | "error_indexing";

export type KBTaskStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type KBSearchMode = "hybrid" | "vector" | "keyword";

export interface KBPresetConfig {
  key: KBChunkPreset;
  chunk_token_num: number;
  overlap_percent: number;
}

export type KBRole = "owner" | "editor" | "viewer" | "subscriber" | "visitor";

export type KBMemberRole = "editor" | "viewer";

export type KBPublicStatus =
  | "none"
  | "pending"
  | "approved"
  | "rejected"
  | "delisted";

export type KBListScope =
  | "mine"
  | "shared"
  | "subscribed"
  | "writable"
  | "readable";

export interface KBConfig {
  enabled: boolean;
  ready: boolean;
  not_ready_reason: "embedding_model_missing" | null;
  presets: KBPresetConfig[];
  allowed_extensions: string[];
  mineru_enabled: boolean;
  max_file_mb: number;
  rerank_available: boolean;
  /** Mirrors `KB_ALLOW_PUBLIC`. */
  allow_public: boolean;
  /** Mirrors `KB_PUBLIC_REQUIRES_REVIEW`. */
  public_requires_review: boolean;
}

export interface KBStats {
  doc_count: number;
  indexed_count: number;
  processing_count: number;
  failed_count: number;
  chunk_count: number;
  size_bytes: number;
}

export interface KBTaskProgress {
  total: number;
  done: number;
  failed: number;
  current_doc_id: string | null;
}

export interface KBTask {
  id: string;
  kb_id: string;
  type: string;
  status: KBTaskStatus;
  progress: KBTaskProgress;
  attempts: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface KBReview {
  action: string;
  reviewer_id: string;
  reviewer_username: string;
  reason: string;
  at: string | null;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  icon: string;
  owner_id: string;
  owner_username: string;
  my_role: KBRole;
  public_status: KBPublicStatus;
  published_at: string | null;
  review: KBReview | null;
  subscriber_count: number;
  can_write: boolean;
  can_manage: boolean;
  can_chat: boolean;
  chunk_preset: KBChunkPreset;
  chunk_token_num: number;
  overlap_percent: number;
  visibility: string;
  graph_enabled: boolean;
  embedding: { model_name: string; dim: number } | null;
  index_outdated: boolean;
  stats: KBStats;
  active_task: KBTask | null;
  created_at: string;
  updated_at: string;
}

export interface KBCreate {
  name: string;
  description?: string;
  icon?: string;
  chunk_preset?: KBChunkPreset;
  chunk_token_num?: number;
  overlap_percent?: number;
}

export type KBUpdate = Partial<KBCreate>;

export interface KBListResponse {
  items: KnowledgeBase[];
}

export interface KBDocumentError {
  stage: "parse" | "index";
  message: string;
  at?: string;
}

export interface KBDocument {
  id: string;
  kb_id: string;
  filename: string;
  ext: string;
  mime: string;
  size: number;
  status: KBDocumentStatus;
  error: KBDocumentError | string | null;
  parse_method: "mineru" | "pdf_text" | "local" | null;
  chunk_count: number;
  token_count: number;
  line_count: number;
  uploader_username: string;
  source: string;
  created_at: string;
  updated_at: string;
  indexed_at: string | null;
}

export interface KBDocumentListParams {
  status?: KBDocumentStatus | "";
  q?: string;
  page?: number;
  page_size?: number;
}

export interface KBDocumentListResponse {
  items: KBDocument[];
  total: number;
  page: number;
  page_size: number;
}

export interface KBUploadResult {
  document: KBDocument;
  duplicate: boolean;
}

export interface KBQuotaLimits {
  kb_max_count: number;
  kb_max_docs_per_kb: number;
  kb_storage_quota_mb: number;
  kb_max_file_size_mb: number;
}

export interface KBQuotaUsage {
  kb_count: number;
  doc_count: number;
  max_docs_in_kb: number;
  storage_bytes: number;
}

export interface KBQuota {
  limits: KBQuotaLimits;
  usage: KBQuotaUsage;
}

export interface KBMember {
  user_id: string;
  username: string;
  role: KBRole;
  granted_by: string;
  created_at: string | null;
}

export interface KBResolvedUser {
  username: string;
  exists: boolean;
  active: boolean;
}

export interface KBResolveUsersResponse {
  items: KBResolvedUser[];
}

export type KBMemberAddErrorReason = "not_found" | "inactive" | "self";

export interface KBShareResult {
  added: KBMember[];
  updated: KBMember[];
  errors: Array<{ username: string; reason: KBMemberAddErrorReason }>;
}

export type KBNotificationType =
  | "kb_shared"
  | "kb_role_changed"
  | "kb_removed"
  | "kb_member_left"
  | "kb_subscribed"
  | "kb_publish_pending"
  | "kb_approved"
  | "kb_rejected"
  | "kb_delisted"
  | "kb_unpublished"
  | (string & {});

export interface KBNotification {
  id: string;
  type: KBNotificationType;
  kb_id: string;
  kb_name: string;
  actor_username: string;
  message: string;
  read: boolean;
  created_at: string | null;
}

export interface KBNotificationListResponse {
  items: KBNotification[];
  total: number;
  unread: number;
  page: number;
  page_size: number;
}

export interface KBPageParams {
  q?: string;
  page?: number;
  page_size?: number;
}

export interface KBPagedKnowledgeBases {
  items: KnowledgeBase[];
  total: number;
  page: number;
  page_size: number;
}

export interface KBImportableFile {
  id: string;
  file_name: string;
  file_type: string;
  mime_type: string;
  file_size: number;
  created_at: string | null;
}

export interface KBImportableFilesResponse {
  items: KBImportableFile[];
  total: number;
  page: number;
  page_size: number;
}

export type KBImportStatus = "created" | "duplicate" | "skipped" | "failed";

export interface KBImportItemResult {
  file_id: string;
  status: KBImportStatus;
  code: string | null;
  message: string | null;
  document: KBDocument | null;
}

export interface KBImportResult {
  results: KBImportItemResult[];
  created: number;
  duplicate: number;
  skipped: number;
  failed: number;
}

export type KBAdminStatus = "pending" | "approved" | "rejected" | "delisted";

export type KBAdminItem = KnowledgeBase;

export interface KBAdminListResponse {
  items: KBAdminItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface KBTaskEnqueueResult {
  task_id: string | null;
  count: number;
}

export interface KBContentLine {
  n: number;
  text: string;
}

export interface KBDocumentContent {
  doc_id: string;
  filename: string;
  total_lines: number;
  offset: number;
  lines: KBContentLine[];
  has_more: boolean;
}

export interface KBChunk {
  chunk_id: string;
  index: number;
  text: string;
  start_line: number;
  end_line: number;
  token_count: number;
}

export interface KBChunkListResponse {
  items: KBChunk[];
  total: number;
  page: number;
  page_size: number;
}

export interface KBSearchRequest {
  query: string;
  top_k?: number;
  mode?: KBSearchMode;
  use_rerank?: boolean | null;
  score_threshold?: number;
  vector_weight?: number;
}

export interface KBSearchHit {
  chunk_id: string;
  kb_id: string;
  kb_name: string;
  doc_id: string;
  filename: string;
  index: number;
  text: string;
  start_line: number;
  end_line: number;
  score: number;
  rerank_score?: number | null;
}

export interface KBSearchResponse {
  results: KBSearchHit[];
  warnings: string[];
  took_ms: number;
}

export interface KBUploadOptions {
  onProgress?: (progress: number, loaded: number, total: number) => void;
}

export interface KBUploadHandle {
  promise: Promise<KBUploadResult>;
  abort: () => void;
}

function getErrorEnvelope(value: unknown): {
  message?: string;
  code?: string;
  detail?: unknown;
} {
  if (!value || typeof value !== "object") return {};
  const payload = value as { detail?: unknown; message?: unknown };
  const detail = payload.detail;
  if (detail && typeof detail === "object") {
    const typed = detail as { message?: unknown; code?: unknown };
    return {
      message: typeof typed.message === "string" ? typed.message : undefined,
      code: typeof typed.code === "string" ? typed.code : undefined,
      detail,
    };
  }
  return {
    message:
      typeof detail === "string"
        ? detail
        : typeof payload.message === "string"
          ? payload.message
          : undefined,
    detail,
  };
}

function buildQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

function kbUrl(kbId: string, suffix = ""): string {
  return `${BASE}/${encodeURIComponent(kbId)}${suffix}`;
}

function docUrl(kbId: string, docId: string, suffix = ""): string {
  return kbUrl(kbId, `/documents/${encodeURIComponent(docId)}${suffix}`);
}

/** Single-file multipart upload via XHR so the UI can show progress and abort. */
function uploadDocument(
  kbId: string,
  file: File,
  options: KBUploadOptions = {},
): KBUploadHandle {
  let xhr = new XMLHttpRequest();
  let aborted = false;

  const promise = new Promise<KBUploadResult>((resolve, reject) => {
    const uploadOnce = async (retried: boolean) => {
      const formData = new FormData();
      formData.append("file", file);

      const token = await getValidAccessToken();
      if (aborted) {
        reject(new Error("Upload was aborted"));
        return;
      }

      xhr = new XMLHttpRequest();

      if (options.onProgress) {
        const onProgress = options.onProgress;
        xhr.upload.addEventListener("progress", (event) => {
          if (event.lengthComputable) {
            onProgress(
              Math.round((event.loaded / event.total) * 100),
              event.loaded,
              event.total,
            );
          }
        });
      }

      xhr.addEventListener("load", async () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as KBUploadResult);
          } catch {
            reject(new Error("Failed to parse upload response"));
          }
          return;
        }

        if (xhr.status === 401 && !retried && getRefreshToken()) {
          try {
            await refreshAccessToken();
          } catch {
            redirectToLogin();
            reject(new Error("Unauthorized"));
            return;
          }
          await uploadOnce(true);
          return;
        }

        let errorData: unknown;
        try {
          errorData = JSON.parse(xhr.responseText);
        } catch {
          errorData = undefined;
        }
        const envelope = getErrorEnvelope(errorData);
        reject(
          new ApiRequestError(
            envelope.message ||
              i18n.t("backendErrors.uploadFailed", {
                status: xhr.status,
                defaultValue: "Upload failed (HTTP {{status}})",
              }),
            xhr.status,
            envelope.code,
            envelope.detail,
          ),
        );
      });

      xhr.addEventListener("error", () => {
        reject(new Error("Network error during upload"));
      });

      xhr.addEventListener("abort", () => {
        aborted = true;
        reject(new Error("Upload was aborted"));
      });

      xhr.open("POST", kbUrl(kbId, "/documents/upload"));
      xhr.withCredentials = true;
      if (token) {
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      }
      xhr.send(formData);
    };

    void uploadOnce(false).catch(reject);
  });

  return {
    promise,
    abort: () => {
      aborted = true;
      xhr.abort();
    },
  };
}

export const knowledgeBaseApi = {
  getConfig(): Promise<KBConfig> {
    return authFetch<KBConfig>(`${BASE}/config`);
  },

  list(scope: KBListScope = "mine"): Promise<KBListResponse> {
    return authFetch<KBListResponse>(`${BASE}${buildQuery({ scope })}`);
  },

  getQuota(): Promise<KBQuota> {
    return authFetch<KBQuota>(`${BASE}/quota`);
  },

  resolveUsers(usernames: string[]): Promise<KBResolveUsersResponse> {
    return authFetch<KBResolveUsersResponse>(`${BASE}/users/resolve`, {
      method: "POST",
      body: JSON.stringify({ usernames }),
    });
  },

  plaza(params: KBPageParams = {}): Promise<KBPagedKnowledgeBases> {
    return authFetch<KBPagedKnowledgeBases>(
      `${BASE}/plaza${buildQuery({ ...params, q: params.q?.trim() })}`,
    );
  },

  listNotifications(
    params: { unread_only?: boolean; page?: number; page_size?: number } = {},
  ): Promise<KBNotificationListResponse> {
    return authFetch<KBNotificationListResponse>(
      `${BASE}/notifications${buildQuery(params)}`,
    );
  },

  getUnreadCount(): Promise<{ count: number }> {
    return authFetch<{ count: number }>(`${BASE}/notifications/unread-count`);
  },

  async markNotificationsRead(ids: string[] | "all"): Promise<void> {
    if (ids === "all") {
      await authFetch<unknown>(`${BASE}/notifications/read-all`, { method: "POST" });
      return;
    }
    await Promise.all(
      ids.map((id) =>
        authFetch<void>(`${BASE}/notifications/${encodeURIComponent(id)}/read`, {
          method: "POST",
        }),
      ),
    );
  },

  listImportableFiles(params: KBPageParams = {}): Promise<KBImportableFilesResponse> {
    return authFetch<KBImportableFilesResponse>(
      `${BASE}/importable-files${buildQuery({ ...params, q: params.q?.trim() })}`,
    );
  },

  importFiles(kbId: string, fileIds: string[]): Promise<KBImportResult> {
    return authFetch<KBImportResult>(kbUrl(kbId, "/documents/import"), {
      method: "POST",
      body: JSON.stringify({ file_ids: fileIds }),
    });
  },

  listMembers(kbId: string): Promise<{ items: KBMember[] }> {
    return authFetch<{ items: KBMember[] }>(kbUrl(kbId, "/members"));
  },

  shareWith(
    kbId: string,
    usernames: string[],
    role: KBMemberRole,
  ): Promise<KBShareResult> {
    return authFetch<KBShareResult>(kbUrl(kbId, "/members"), {
      method: "POST",
      body: JSON.stringify({ usernames, role }),
    });
  },

  updateMemberRole(kbId: string, userId: string, role: KBMemberRole): Promise<KBMember> {
    return authFetch<KBMember>(kbUrl(kbId, `/members/${encodeURIComponent(userId)}`), {
      method: "PATCH",
      body: JSON.stringify({ role }),
    });
  },

  async removeMember(kbId: string, userId: string): Promise<void> {
    await authFetch<void>(kbUrl(kbId, `/members/${encodeURIComponent(userId)}`), {
      method: "DELETE",
    });
  },

  /** Leaving a share is removing yourself from the member list. */
  leave(kbId: string, myUserId: string): Promise<void> {
    return knowledgeBaseApi.removeMember(kbId, myUserId);
  },

  subscribe(kbId: string): Promise<KnowledgeBase> {
    return authFetch<KnowledgeBase>(kbUrl(kbId, "/subscribe"), { method: "POST" });
  },

  async unsubscribe(kbId: string): Promise<void> {
    await authFetch<void>(kbUrl(kbId, "/subscribe"), { method: "DELETE" });
  },

  publish(kbId: string): Promise<KnowledgeBase> {
    return authFetch<KnowledgeBase>(kbUrl(kbId, "/publish"), { method: "POST" });
  },

  unpublish(kbId: string): Promise<KnowledgeBase> {
    return authFetch<KnowledgeBase>(kbUrl(kbId, "/unpublish"), { method: "POST" });
  },

  create(data: KBCreate): Promise<KnowledgeBase> {
    return authFetch<KnowledgeBase>(BASE, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  get(kbId: string): Promise<KnowledgeBase> {
    return authFetch<KnowledgeBase>(kbUrl(kbId));
  },

  update(kbId: string, data: KBUpdate): Promise<KnowledgeBase> {
    return authFetch<KnowledgeBase>(kbUrl(kbId), {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  async remove(kbId: string): Promise<void> {
    await authFetch<void>(kbUrl(kbId), { method: "DELETE" });
  },

  listDocuments(
    kbId: string,
    params: KBDocumentListParams = {},
  ): Promise<KBDocumentListResponse> {
    return authFetch<KBDocumentListResponse>(
      kbUrl(
        kbId,
        `/documents${buildQuery({
          status: params.status,
          q: params.q?.trim(),
          page: params.page,
          page_size: params.page_size,
        })}`,
      ),
    );
  },

  uploadDocument,

  retryDocuments(kbId: string, docIds: string[] = []): Promise<KBTaskEnqueueResult> {
    return authFetch<KBTaskEnqueueResult>(kbUrl(kbId, "/documents/retry"), {
      method: "POST",
      body: JSON.stringify({ doc_ids: docIds }),
    });
  },

  deleteDocuments(kbId: string, docIds: string[]): Promise<{ deleted: number }> {
    return authFetch<{ deleted: number }>(kbUrl(kbId, "/documents/delete"), {
      method: "POST",
      body: JSON.stringify({ doc_ids: docIds }),
    });
  },

  getDocument(kbId: string, docId: string): Promise<KBDocument> {
    return authFetch<KBDocument>(docUrl(kbId, docId));
  },

  getDocumentContent(
    kbId: string,
    docId: string,
    params: { offset?: number; limit?: number } = {},
  ): Promise<KBDocumentContent> {
    return authFetch<KBDocumentContent>(
      docUrl(kbId, docId, `/content${buildQuery(params)}`),
    );
  },

  getDocumentChunks(
    kbId: string,
    docId: string,
    params: { page?: number; page_size?: number } = {},
  ): Promise<KBChunkListResponse> {
    return authFetch<KBChunkListResponse>(
      docUrl(kbId, docId, `/chunks${buildQuery(params)}`),
    );
  },

  /** The download route needs the bearer token, so fetch a blob instead of using an anchor href. */
  async downloadDocument(kbId: string, doc: Pick<KBDocument, "id" | "filename">): Promise<void> {
    const response = await authenticatedRequest(docUrl(kbId, doc.id, "/download"));
    if (!response.ok) {
      const errorData: unknown = await response.json().catch(() => undefined);
      const envelope = getErrorEnvelope(errorData);
      throw new ApiRequestError(
        envelope.message ||
          i18n.t("backendErrors.requestFailed", {
            status: response.status,
            defaultValue: "Request failed (HTTP {{status}})",
          }),
        response.status,
        envelope.code,
        envelope.detail,
      );
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = doc.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },

  search(kbId: string, data: KBSearchRequest): Promise<KBSearchResponse> {
    return authFetch<KBSearchResponse>(kbUrl(kbId, "/search"), {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  listTasks(kbId: string, active = true): Promise<{ items: KBTask[] }> {
    return authFetch<{ items: KBTask[] }>(
      kbUrl(kbId, `/tasks${buildQuery({ active })}`),
    );
  },

  cancelTask(kbId: string, taskId: string): Promise<KBTask> {
    return authFetch<KBTask>(
      kbUrl(kbId, `/tasks/${encodeURIComponent(taskId)}/cancel`),
      { method: "POST" },
    );
  },

  reindex(kbId: string): Promise<KBTaskEnqueueResult> {
    return authFetch<KBTaskEnqueueResult>(kbUrl(kbId, "/reindex"), {
      method: "POST",
    });
  },
};

const ADMIN_BASE = `${API_BASE}/api/admin/kb`;

export const knowledgeBaseAdminApi = {
  list(
    params: KBPageParams & { status?: KBAdminStatus },
  ): Promise<KBAdminListResponse> {
    return authFetch<KBAdminListResponse>(
      `${ADMIN_BASE}/public${buildQuery({ ...params, q: params.q?.trim() })}`,
    );
  },

  approve(kbId: string): Promise<KBAdminItem> {
    return authFetch<KBAdminItem>(`${ADMIN_BASE}/${encodeURIComponent(kbId)}/approve`, {
      method: "POST",
    });
  },

  reject(kbId: string, reason: string): Promise<KBAdminItem> {
    return authFetch<KBAdminItem>(`${ADMIN_BASE}/${encodeURIComponent(kbId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  },

  delist(kbId: string, reason: string): Promise<KBAdminItem> {
    return authFetch<KBAdminItem>(`${ADMIN_BASE}/${encodeURIComponent(kbId)}/delist`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  },
};
