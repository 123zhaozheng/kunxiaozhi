/**
 * Upload API - 文件上传
 */

import type { FileCheckResult, UploadConfig, UploadResult } from "../../types";
import { API_BASE, getFullUrl } from "./config";
import { ApiRequestError, authFetch } from "./fetch";
import { authenticatedRequest } from "./authenticatedRequest";
import {
  getValidAccessToken,
  redirectToLogin,
  refreshAccessToken,
} from "./tokenManager";
import { getRefreshToken } from "./token";

interface SignedUrlItem {
  key: string;
  url: string | null;
  error?: string;
}

function getErrorEnvelope(value: unknown): {
  message?: string;
  code?: string;
  detail?: unknown;
} {
  if (!value || typeof value !== "object") return {};
  const payload = value as { detail?: unknown; message?: unknown; code?: unknown };
  const detail = payload.detail;
  if (detail && typeof detail === "object") {
    const typed = detail as { message?: unknown; code?: unknown; error?: unknown };
    return {
      message:
        typeof typed.message === "string" ? typed.message : undefined,
      code:
        typeof typed.code === "string"
          ? typed.code
          : typeof typed.error === "string"
            ? typed.error
            : undefined,
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
    code: typeof payload.code === "string" ? payload.code : undefined,
    detail,
  };
}

export interface UploadOptions {
  folder?: string;
  managedAsset?: {
    kind: "persona" | "team";
    ownerRef: string;
  };
  onProgress?: (progress: number, loaded: number, total: number) => void;
}

export interface UploadHandle {
  promise: Promise<UploadResult>;
  abort: () => void;
}

let _configPromise: Promise<UploadConfig> | null = null;

export const uploadApi = {
  /**
   * 上传文件
   * @param file - The file to upload
   * @param folderOrOptions - Either a folder string (for backward compatibility) or UploadOptions object
   */
  uploadFile(
    file: File,
    folderOrOptions: string | UploadOptions = "uploads",
  ): UploadHandle {
    // Handle backward compatibility: string folder or options object
    const options: UploadOptions =
      typeof folderOrOptions === "string"
        ? { folder: folderOrOptions }
        : folderOrOptions;

    const folder = options.folder || "uploads";
    const { onProgress } = options;

    let xhr = new XMLHttpRequest();
    let aborted = false;

    const promise = new Promise<UploadResult>((resolve, reject) => {
      const uploadOnce = async (retried: boolean) => {
        const formData = new FormData();
        formData.append("file", file);

        const token = await getValidAccessToken();
        if (aborted) {
          reject(new Error("Upload was aborted"));
          return;
        }

        xhr = new XMLHttpRequest();

        if (onProgress) {
          xhr.upload.addEventListener("progress", (event) => {
            if (event.lengthComputable) {
              const progress = Math.round((event.loaded / event.total) * 100);
              onProgress(progress, event.loaded, event.total);
            }
          });
        }

        xhr.addEventListener("load", async () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              const raw = JSON.parse(xhr.responseText);
              const result: UploadResult = {
                key: raw.key,
                url: raw.url,
                name: raw.name,
                type: raw.type,
                mimeType: raw.mimeType ?? raw.mime_type ?? "",
                size: raw.size,
                fileId: raw.file_id ?? raw.fileId,
                source: raw.source,
                status: raw.status,
                storageUsage: raw.storage_usage ?? raw.storageUsage,
              };
              resolve(result);
            } catch {
              reject(new Error("Failed to parse upload response"));
            }
            return;
          }

          if (xhr.status === 401 && !retried && getRefreshToken()) {
            try {
              await refreshAccessToken();
              await uploadOnce(true);
              return;
            } catch {
              redirectToLogin();
            }
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
              envelope.message || `Upload failed: ${xhr.statusText}`,
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

        const url = options.managedAsset
          ? `${API_BASE}/api/upload/asset/${options.managedAsset.kind}/${encodeURIComponent(options.managedAsset.ownerRef)}`
          : `${API_BASE}/api/upload/file?folder=${encodeURIComponent(folder)}`;
        xhr.open("POST", url);
        xhr.withCredentials = true;

        if (token) {
          xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        }

        xhr.send(formData);
      };

      void uploadOnce(false);
    });

    return {
      promise,
      abort: () => {
        aborted = true;
        xhr.abort();
      },
    };
  },

  /**
   * Check if file already exists by hash (for deduplication)
   */
  async checkFile(
    hash: string,
    size: number,
    name: string,
    mimeType: string,
  ): Promise<FileCheckResult> {
    const res = await authenticatedRequest(`${API_BASE}/api/upload/check`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({ hash, size, name, mime_type: mimeType }),
    });
    if (!res.ok) {
      throw new Error(`Check failed: ${res.status}`);
    }
    const data = await res.json();
    if (!data.exists) {
      return { exists: false };
    }
    return {
      ...data,
      mimeType: data.mime_type || data.mimeType,
      fileId: data.file_id || data.fileId,
      source: data.source,
      status: data.status,
      storageUsage: data.storage_usage || data.storageUsage,
    };
  },

  /**
   * 上传头像
   */
  async uploadAvatar(file: File): Promise<UploadResult> {
    const formData = new FormData();
    formData.append("file", file);

    const response = await authenticatedRequest(
      `${API_BASE}/api/upload/avatar`,
      {
        method: "POST",
        body: formData,
        credentials: "include",
      },
    );

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.detail || `Upload failed: ${response.statusText}`,
      );
    }

    return response.json();
  },

  /**
   * 删除头像
   */
  async deleteAvatar(): Promise<{ deleted: boolean }> {
    const response = await authenticatedRequest(
      `${API_BASE}/api/upload/avatar`,
      {
        method: "DELETE",
        credentials: "include",
      },
    );

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.detail || `Delete failed: ${response.statusText}`,
      );
    }

    return response.json();
  },

  /**
   * 获取存储配置
   */
  async getConfig(): Promise<UploadConfig> {
    if (!_configPromise) {
      _configPromise = authFetch<UploadConfig>(`${API_BASE}/api/upload/config`);
    }
    return _configPromise;
  },

  /**
   * 获取 S3 签名 URL（用于访问私有文件）
   */
  async getSignedUrl(key: string, expires: number = 3600): Promise<string> {
    const result = await authFetch<SignedUrlItem>(
      `${API_BASE}/api/upload/signed-url?key=${encodeURIComponent(
        key,
      )}&expires=${expires}`,
    );
    if (result.error || !result.url) {
      throw new Error(result.error || "Failed to get signed URL");
    }
    return getFullUrl(result.url) || result.url;
  },

  /**
   * 批量获取 S3 签名 URL
   */
  async getSignedUrls(
    keys: string[],
    expires: number = 3600,
  ): Promise<{ urls: SignedUrlItem[]; expires_in: number }> {
    const result = await authFetch<{
      urls: SignedUrlItem[];
      expires_in: number;
    }>(`${API_BASE}/api/upload/signed-urls`, {
      method: "POST",
      body: JSON.stringify({ keys, expires }),
    });
    return {
      ...result,
      urls: result.urls.map((item) => ({
        ...item,
        url: item.url ? getFullUrl(item.url) || item.url : item.url,
      })),
    };
  },

  /**
   * 删除上传的文件
   */
  async deleteFile(key: string): Promise<{ deleted: boolean; key: string }> {
    const response = await authenticatedRequest(
      `${API_BASE}/api/upload/${encodeURIComponent(key)}`,
      {
        method: "DELETE",
        credentials: "include",
      },
    );

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        errorData.detail || `Delete failed: ${response.statusText}`,
      );
    }

    return response.json();
  },
};
