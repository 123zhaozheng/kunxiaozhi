import { memo, useEffect, useMemo, useState } from "react";
import { Ban, Loader2, RotateCcw, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import type { MessageAttachment } from "../../types";
import { isDeletedAttachmentStatus } from "../../types/storage";
import { ImageWithSkeleton } from "../chat/ChatMessage/ImageWithSkeleton";
import { ExcalidrawThumbnail } from "./ExcalidrawThumbnail";
import {
  getFileTypeInfo,
  formatFileSize as formatFileSizeUtil,
  isExcalidrawFile,
} from "../documents/utils";
import { getFullUrl } from "../../services/api";
import {
  STORAGE_LIFECYCLE_EVENT,
  matchesStorageLifecycleEvent,
  type StorageLifecycleEventDetail,
} from "../../services/storageLifecycle";

// Re-export formatFileSize for external use
// eslint-disable-next-line react-refresh/only-export-components
export const formatFileSize = formatFileSizeUtil;

// Re-export for backward compatibility
// eslint-disable-next-line react-refresh/only-export-components
export function getAttachmentIconInfo(
  mimeType: string,
  fileName?: string,
): {
  icon: React.ElementType;
  bgColor: string;
  iconColor: string;
  label: string;
} {
  const info = getFileTypeInfo(fileName || "", mimeType);
  return {
    icon: info.icon,
    bgColor: info.bg,
    iconColor: info.color,
    label: info.label,
  };
}

export interface AttachmentCardProps {
  attachment: MessageAttachment;
  /** Click callback for preview. Ignored for deleted attachments. */
  onClick?: () => void;
  /** Delete button callback. */
  onRemove?: () => void;
  /** Cancel upload button callback. */
  onCancel?: () => void;
  /** Retry a retained upload after a quota or admission error. */
  onRetry?: () => void;
  /** Display mode: editable shows removal controls, preview shows a card. */
  variant?: "editable" | "preview";
  /** Compact size for the composer. */
  size?: "default" | "compact";
  /** Whether upload is in progress. */
  isUploading?: boolean;
}

export const AttachmentCard = memo(function AttachmentCard({
  attachment,
  onClick,
  onRemove,
  onCancel,
  onRetry,
  variant = "preview",
  size = "default",
  isUploading = false,
}: AttachmentCardProps) {
  const { t } = useTranslation();
  const [lifecycleStatus, setLifecycleStatus] = useState(
    attachment.lifecycleStatus ?? attachment.status,
  );
  const [available, setAvailable] = useState(attachment.available);

  useEffect(() => {
    setLifecycleStatus(attachment.lifecycleStatus ?? attachment.status);
    setAvailable(attachment.available);
  }, [attachment.available, attachment.lifecycleStatus, attachment.status]);

  useEffect(() => {
    const handleLifecycle = (event: Event) => {
      const detail = (event as CustomEvent<StorageLifecycleEventDetail>).detail;
      if (
        !matchesStorageLifecycleEvent(detail, attachment.fileId, attachment.key)
      ) {
        return;
      }
      setLifecycleStatus(detail.status);
      if (detail.status === "deleted" || detail.status === "delete_pending") {
        setAvailable(false);
      }
    };
    window.addEventListener(STORAGE_LIFECYCLE_EVENT, handleLifecycle);
    return () => window.removeEventListener(STORAGE_LIFECYCLE_EVENT, handleLifecycle);
  }, [attachment.fileId, attachment.key]);

  const {
    icon: FileIcon,
    bgColor,
    iconColor,
    label,
  } = getAttachmentIconInfo(attachment.mimeType, attachment.name);
  const isDeleted = isDeletedAttachmentStatus({
    ...attachment,
    lifecycleStatus,
    available,
  });
  const hasUploadError = Boolean(attachment.uploadError);
  const lifecycleError = attachment.lifecycleError;
  const isLifecycleUnavailable = [
    "pending",
    "delete_pending",
    "forbidden",
    "missing",
    "transient",
  ].includes(String(lifecycleStatus ?? ""));
  const isUnavailable = isDeleted || hasUploadError || isLifecycleUnavailable;
  const attachmentUrl = !isUnavailable && attachment.url
    ? getFullUrl(attachment.url) ?? attachment.url
    : "";
  const isImage =
    !isUnavailable &&
    attachment.mimeType?.startsWith("image/") &&
    Boolean(attachmentUrl);
  const fileExt = useMemo(() => {
    const idx = attachment.name?.lastIndexOf(".");
    return idx != null && idx > 0
      ? attachment.name.slice(idx + 1).toLowerCase()
      : "";
  }, [attachment.name]);
  const isExcalidraw =
    !isUnavailable && isExcalidrawFile(fileExt) && Boolean(attachmentUrl);
  const isThumbnail = isImage || isExcalidraw;
  const isCompact = size === "compact";

  const handleClick = () => {
    if (!isUnavailable) onClick?.();
  };

  const handleRemove = (event: React.MouseEvent) => {
    event.stopPropagation();
    onRemove?.();
  };

  const handleRetry = (event: React.MouseEvent) => {
    event.stopPropagation();
    onRetry?.();
  };

  const renderVisual = (compact: boolean) => (
    <div
      className={clsx(
        "shrink-0 flex items-center justify-center overflow-hidden",
        compact ? "rounded-lg size-10" : "size-12 sm:size-14 rounded-l-2xl sm:rounded-l-xl",
        isThumbnail ? "relative" : bgColor,
        isUnavailable && "bg-red-50 dark:bg-red-950/30",
      )}
    >
      {isDeleted ? (
        <Trash2 size={compact ? 18 : 20} className="text-red-500 dark:text-red-400" />
      ) : hasUploadError ? (
        <Ban size={compact ? 18 : 20} className="text-amber-500 dark:text-amber-400" />
      ) : isLifecycleUnavailable ? (
        <Ban size={compact ? 18 : 20} className="text-amber-500 dark:text-amber-400" />
      ) : isUploading ? (
        <Loader2 size={18} className={clsx(iconColor, "animate-spin")} />
      ) : isImage ? (
        compact ? (
          <ImageWithSkeleton
            src={attachmentUrl}
            alt={attachment.name}
            skipUrlResolve
            inline
          />
        ) : (
          <img
            src={attachmentUrl}
            alt={attachment.name}
            referrerPolicy="no-referrer"
            className="w-full h-full object-cover"
          />
        )
      ) : isExcalidraw ? (
        <ExcalidrawThumbnail
          url={attachmentUrl}
          alt={attachment.name}
          className={compact ? undefined : "w-full h-full object-cover"}
        />
      ) : (
        <FileIcon size={18} className={iconColor} />
      )}
    </div>
  );

  const renderStatus = (compact: boolean) => (
    <>
      <span
        className={clsx(
          compact
            ? "text-[13px] font-medium truncate max-w-[120px] sm:max-w-[160px] leading-tight"
            : "text-[13px] sm:text-sm font-medium truncate leading-tight",
          isDeleted
            ? "text-red-700 dark:text-red-300 line-through decoration-red-500 decoration-2"
            : "text-stone-800 dark:text-stone-100",
        )}
      >
        {attachment.name}
      </span>
      <span
        className={clsx(
          "mt-0.5",
          compact
            ? "text-xs text-stone-400 dark:text-stone-500"
            : "flex items-center justify-between text-[11px] sm:text-xs text-stone-400 dark:text-stone-500 sm:mt-1",
        )}
      >
        {isDeleted ? (
          <span className="inline-flex items-center gap-1 font-medium text-red-600 dark:text-red-400">
            <Trash2 size={compact ? 12 : 13} aria-hidden="true" />
            {t("storage.deleted", "Deleted")}
          </span>
        ) : hasUploadError ? (
          <span className="inline-flex max-w-full items-center gap-1 text-amber-700 dark:text-amber-300">
            <Ban size={compact ? 12 : 13} aria-hidden="true" />
            <span className="truncate">{attachment.uploadError}</span>
          </span>
        ) : isLifecycleUnavailable ? (
          <span className="inline-flex max-w-full items-center gap-1 text-amber-700 dark:text-amber-300">
            <Ban size={compact ? 12 : 13} aria-hidden="true" />
            <span className="truncate">
              {t(
                `storage.fileStatuses.${lifecycleStatus}`,
                lifecycleError || t("storage.fileUnavailable", "File unavailable"),
              )}
            </span>
          </span>
        ) : compact ? (
          isUploading
            ? `${attachment.uploadProgress ?? 0}%`
            : formatFileSize(attachment.size)
        ) : (
          <>
            <span className="capitalize truncate">{label}</span>
            <span className="shrink-0 ml-2">
              {isUploading
                ? t("fileUpload.uploading")
                : formatFileSize(attachment.size)}
            </span>
          </>
        )}
      </span>
    </>
  );

  const action =
    variant === "editable" && !isDeleted
      ? isUploading && onCancel
        ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onCancel();
              }}
              className="shrink-0 size-6 rounded-full flex items-center justify-center bg-red-100/80 dark:bg-red-900/30 text-red-500 dark:text-red-400"
              title={t("fileUpload.cancelUpload")}
              aria-label={t("fileUpload.cancelUpload")}
            >
              <X size={12} />
            </button>
          )
        : hasUploadError && onRetry
          ? (
              <button
                type="button"
                onClick={handleRetry}
                className="shrink-0 inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium text-amber-700 dark:text-amber-300 bg-amber-100/80 dark:bg-amber-900/30"
                title={t("storage.retryUpload", "Retry upload")}
              >
                <RotateCcw size={12} />
                {t("common.retry", "Retry")}
              </button>
            )
          : onRemove && (
              <button
                type="button"
                onClick={handleRemove}
                className="shrink-0 size-6 rounded-full flex items-center justify-center bg-stone-100/80 dark:bg-stone-700/80 text-stone-400 dark:text-stone-500 hover:bg-red-100 dark:hover:bg-red-900/30 hover:text-red-500 dark:hover:text-red-400"
                title={t("fileUpload.removeAttachment")}
                aria-label={t("fileUpload.removeAttachment")}
              >
                <X size={12} />
              </button>
            )
      : null;

  if (isCompact) {
    return (
      <div
        onClick={handleClick}
        role={isUnavailable ? "group" : undefined}
        aria-disabled={isUnavailable ? true : undefined}
        data-lifecycle-status={lifecycleStatus}
        className={clsx(
          "group relative flex items-center gap-2.5 px-3 py-2 rounded-xl border",
          "border-stone-200/60 dark:border-stone-700/60 bg-gradient-to-br from-white to-stone-50/80 dark:from-stone-800 dark:to-stone-900 shadow-sm select-none",
          !isUnavailable && "cursor-pointer transition-all duration-200 hover:shadow-md hover:-translate-y-0.5 active:scale-[0.98]",
          isUnavailable && "cursor-not-allowed border-red-200/80 dark:border-red-900/50",
          isUploading && !onCancel && "pointer-events-none",
        )}
      >
        {renderVisual(true)}
        <div className="flex flex-col min-w-0 flex-1">{renderStatus(true)}</div>
        {action}
      </div>
    );
  }

  const cardContent = (
    <>
      {renderVisual(false)}
      <div className="flex flex-col justify-center px-3 sm:px-3.5 py-2 min-w-0 flex-1">
        {renderStatus(false)}
      </div>
    </>
  );

  const className = clsx(
    "group relative flex items-center overflow-hidden h-12 sm:h-14 min-w-[200px] max-w-[280px] sm:min-w-[240px] sm:max-w-[320px]",
    "bg-gradient-to-br from-white to-stone-50/80 dark:from-stone-800 dark:to-stone-900 rounded-2xl sm:rounded-xl border shadow-sm text-left select-none",
    isUnavailable
      ? "border-red-200/80 dark:border-red-900/50 cursor-not-allowed"
      : "border-stone-200/60 dark:border-stone-700/60 cursor-pointer transition-all duration-300 hover:shadow-lg hover:-translate-y-0.5 hover:scale-[1.02] active:scale-[0.98]",
    isUploading && "pointer-events-none",
  );

  if (isUnavailable) {
    return (
      <div
        onClick={handleClick}
        role="group"
        aria-disabled="true"
        data-lifecycle-status={lifecycleStatus}
        className={className}
      >
        {cardContent}
        {action}
      </div>
    );
  }

  return (
    <button
      onClick={handleClick}
      className={className}
      type="button"
      aria-disabled={isUploading ? true : undefined}
    >
      {cardContent}
      {action}
    </button>
  );
});
