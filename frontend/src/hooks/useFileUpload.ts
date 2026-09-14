import { useState, useCallback, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import {
  uploadApi,
  storageApi,
  getStorageErrorCode,
  getStorageErrorMessage,
} from "../services/api";
import { buildApiUrl } from "../services/api/config";
import type { FileCheckResult, UploadResult } from "../types";
import { compressImageFile } from "../utils/imageCompression";
import { uuid } from "../utils/uuid";
import type { MessageAttachment, FileCategory } from "../types";
import { isStorageQuotaError } from "../types/storage";
import {
  dispatchStorageLifecycleEvent,
  requestStorageManagement,
} from "../services/storageLifecycle";

export interface UploadLimits {
  image: number;
  video: number;
  audio: number;
  document: number;
  maxFiles: number;
}

export interface UseFileUploadOptions {
  attachments: MessageAttachment[];
  onAttachmentsChange: (
    attachments:
      | MessageAttachment[]
      | ((prev: MessageAttachment[]) => MessageAttachment[]),
  ) => void;
  /** Optional host callback; the shared event remains the default integration. */
  onStorageManagementOpen?: () => void;
}

interface PendingUpload {
  file: File;
  category: FileCategory;
}

function getFileCategory(file: File): FileCategory {
  const type = file.type.toLowerCase();
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("video/")) return "video";
  if (type.startsWith("audio/")) return "audio";
  return "document";
}

function computeFileHash(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(
      new URL("../workers/hashWorker.ts", import.meta.url),
      { type: "module" },
    );
    worker.onmessage = (e) => {
      worker.terminate();
      if (e.data.error) {
        reject(new Error(e.data.error));
      } else {
        resolve(e.data.hash);
      }
    };
    worker.onerror = (e) => {
      worker.terminate();
      reject(new Error(e.message));
    };
    worker.postMessage({ file });
  });
}

export function useFileUpload({
  attachments,
  onAttachmentsChange,
  onStorageManagementOpen,
}: UseFileUploadOptions) {
  const { t } = useTranslation();
  const [uploadLimits, setUploadLimits] = useState<UploadLimits | null>(null);
  const limitsFetched = useRef(false);
  const abortMapRef = useRef<Map<string, () => void>>(new Map());
  const pendingFilesRef = useRef<Map<string, PendingUpload>>(new Map());
  const isMountedRef = useRef(true);

  useEffect(() => {
    const abortMap = abortMapRef.current;
    const pendingFiles = pendingFilesRef.current;
    return () => {
      isMountedRef.current = false;
      for (const abort of abortMap.values()) {
        abort();
      }
      abortMap.clear();
      pendingFiles.clear();
    };
  }, []);

  // Fetch static upload limits once. Mutable quota data is fetched per upload.
  useEffect(() => {
    if (limitsFetched.current) return;

    limitsFetched.current = true;
    let isMounted = true;

    uploadApi
      .getConfig()
      .then((config) => {
        if (isMounted && config.uploadLimits) {
          setUploadLimits(config.uploadLimits);
        }
      })
      .catch(() => {});

    return () => {
      isMounted = false;
    };
  }, []);

  const validateSize = useCallback(
    (file: File, category: FileCategory): boolean => {
      if (!uploadLimits) return true;
      const maxMB = uploadLimits[category];
      if (file.size > maxMB * 1024 * 1024) {
        toast.error(`${t("fileUpload.fileTooLarge")} (${maxMB}MB)`);
        return false;
      }
      return true;
    },
    [uploadLimits, t],
  );

  const validateCount = useCallback(
    (newFileCount: number): boolean => {
      if (!uploadLimits) return true;
      const remaining = uploadLimits.maxFiles - attachments.length;
      if (remaining <= 0 || newFileCount > remaining) {
        toast.error(
          t("fileUpload.tooManyFiles", { count: uploadLimits.maxFiles }),
        );
        return false;
      }
      return true;
    },
    [uploadLimits, attachments.length, t],
  );

  const notifyStorageManagement = useCallback(() => {
    requestStorageManagement();
    onStorageManagementOpen?.();
  }, [onStorageManagementOpen]);

  const markQuotaBlocked = useCallback(
    (tempId: string, error: unknown) => {
      const code = getStorageErrorCode(error);
      const message =
        code === "storage_quota_exceeded" ||
        code === "storage_operation_too_large"
          ? t(
              "storage.quotaExceeded",
              "Storage space is full. Free space and try again.",
            )
          : getStorageErrorMessage(
              error,
              t(
                "storage.quotaExceeded",
                "Storage space is full. Free space and try again.",
              ),
            );
      onAttachmentsChange((prev) =>
        prev.map((attachment) =>
          attachment.id === tempId
            ? {
                ...attachment,
                isUploading: false,
                uploadError: message,
                uploadProgress: 0,
              }
            : attachment,
        ),
      );
      notifyStorageManagement();
    },
    [notifyStorageManagement, onAttachmentsChange, t],
  );

  const cancelUpload = useCallback(
    (id: string) => {
      const abort = abortMapRef.current.get(id);
      if (abort) {
        abort();
        abortMapRef.current.delete(id);
      }
      pendingFilesRef.current.delete(id);
      onAttachmentsChange((prev) => prev.filter((a) => a.id !== id));
    },
    [onAttachmentsChange],
  );

  const uploadFile = useCallback(
    (file: File, category?: FileCategory) => {
      const fileCategory = category || getFileCategory(file);

      const maybeCompress =
        fileCategory === "image"
          ? compressImageFile(file).catch(() => file)
          : Promise.resolve(file);

      void maybeCompress.then(async (processedFile) => {
        if (!isMountedRef.current) return;

        const tempId = `temp-${uuid()}`;
        pendingFilesRef.current.set(tempId, {
          file: processedFile,
          category: fileCategory,
        });

        const tempAttachment: MessageAttachment = {
          id: tempId,
          key: "",
          name: processedFile.name,
          type: fileCategory,
          mimeType: processedFile.type,
          size: processedFile.size,
          url: "",
          uploadProgress: 0,
          isUploading: true,
        };
        onAttachmentsChange((prev) => [...prev, tempAttachment]);

        // This is only an early UX gate. The upload response remains the
        // authoritative decision, so a stale summary never grants access.
        try {
          const usage = await storageApi.getUsage();
          const clearlyOverLimit =
            usage.status === "full" ||
            usage.status === "over_quota" ||
            processedFile.size > usage.remaining_bytes;
          if (clearlyOverLimit) {
            markQuotaBlocked(tempId, {
              detail: {
                code: "storage_quota_exceeded",
                message: t(
                  "storage.quotaExceeded",
                  "Storage space is full. Free space and try again.",
                ),
                usage,
              },
            });
            return;
          }
        } catch {
          // Compatibility with deployments before the storage endpoint exists.
        }

        try {
          const hash = await computeFileHash(processedFile);
          if (!isMountedRef.current) throw new Error("Upload was aborted");

          onAttachmentsChange((prev: MessageAttachment[]) =>
            prev.map((a) =>
              a.id === tempId ? { ...a, uploadProgress: 1 } : a,
            ),
          );

          let check: FileCheckResult;
          try {
            check = await uploadApi.checkFile(
              hash,
              processedFile.size,
              processedFile.name,
              processedFile.type,
            );
          } catch (error) {
            if (isStorageQuotaError(error)) throw error;
            check = { exists: false };
          }

          if (!isMountedRef.current) return;

          if (check.exists && "key" in check) {
            abortMapRef.current.delete(tempId);
            pendingFilesRef.current.delete(tempId);
            const c = check as FileCheckResult;
            const finalAttachment: MessageAttachment = {
              id: uuid(),
              key: c.key ?? "",
              name: c.name || processedFile.name,
              type: c.type as FileCategory,
              mimeType: c.mimeType ?? processedFile.type,
              size: c.size ?? processedFile.size,
              url: buildApiUrl(c.url || `/api/upload/file/${c.key ?? ""}`),
              fileId: c.fileId,
              source: c.source,
              lifecycleStatus: c.status,
            };
            onAttachmentsChange((prev: MessageAttachment[]) =>
              prev.map((a) =>
                a.id === tempId
                  ? {
                      ...finalAttachment,
                      uploadProgress: 100,
                      isUploading: false,
                    }
                  : a,
              ),
            );
            if (c.fileId) {
              dispatchStorageLifecycleEvent({
                fileIds: [c.fileId],
                keys: c.key ? [c.key] : undefined,
                status: c.status,
                usage: c.storageUsage,
              });
            }
            return;
          }

          const handle = uploadApi.uploadFile(processedFile, {
            onProgress: (progress) => {
              if (!isMountedRef.current) return;
              onAttachmentsChange((prev: MessageAttachment[]) =>
                prev.map((a) =>
                  a.id === tempId
                    ? { ...a, uploadProgress: progress, isUploading: true }
                    : a,
                ),
              );
            },
          });

          abortMapRef.current.set(tempId, handle.abort);
          const result: UploadResult = await handle.promise;
          if (!isMountedRef.current) return;

          abortMapRef.current.delete(tempId);
          pendingFilesRef.current.delete(tempId);
          const finalAttachment: MessageAttachment = {
            id: uuid(),
            key: result.key,
            name: result.name || processedFile.name,
            type: result.type as FileCategory,
            mimeType: result.mimeType,
            size: result.size,
            url: buildApiUrl(result.url),
            fileId: result.fileId,
            source: result.source,
            lifecycleStatus: result.status,
          };
          onAttachmentsChange((prev: MessageAttachment[]) =>
            prev.map((a) => (a.id === tempId ? finalAttachment : a)),
          );
          if (result.fileId) {
            dispatchStorageLifecycleEvent({
              fileIds: [result.fileId],
              keys: result.key ? [result.key] : undefined,
              status: result.status,
              usage: result.storageUsage,
            });
          }
        } catch (error) {
          abortMapRef.current.delete(tempId);
          if (!isMountedRef.current) return;
          if (error instanceof Error && error.message === "Upload was aborted") {
            return;
          }
          if (isStorageQuotaError(error)) {
            markQuotaBlocked(tempId, error);
            return;
          }

          pendingFilesRef.current.delete(tempId);
          console.error("Upload failed:", error);
          toast.error(
            error instanceof Error
              ? error.message
              : t("fileUpload.uploadFailed"),
          );
          onAttachmentsChange((prev: MessageAttachment[]) =>
            prev.filter((a) => a.id !== tempId),
          );
        }
      });
    },
    [markQuotaBlocked, onAttachmentsChange, t],
  );

  const retryUpload = useCallback(
    (id: string) => {
      const pending = pendingFilesRef.current.get(id);
      if (!pending) return;
      pendingFilesRef.current.delete(id);
      onAttachmentsChange((prev) => prev.filter((attachment) => attachment.id !== id));
      uploadFile(pending.file, pending.category);
    },
    [onAttachmentsChange, uploadFile],
  );

  const uploadFiles = useCallback(
    (files: FileList | File[], category?: FileCategory) => {
      const fileArray = Array.from(files);
      if (fileArray.length === 0) return;

      if (!validateCount(fileArray.length)) return;

      for (const file of fileArray) {
        const fileCategory = category || getFileCategory(file);
        if (!validateSize(file, fileCategory)) continue;
        uploadFile(file, fileCategory);
      }
    },
    [validateCount, validateSize, uploadFile],
  );

  return {
    uploadLimits,
    uploadFiles,
    uploadFile,
    retryUpload,
    validateSize,
    validateCount,
    cancelUpload,
  };
}

export { getFileCategory };
