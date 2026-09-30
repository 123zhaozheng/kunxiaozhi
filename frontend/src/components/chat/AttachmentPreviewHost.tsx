import { useEffect, useRef, useState } from "react";
import { DelayedUnmount } from "../common/DelayedUnmount";
import { getFullUrl } from "../../services/api";
import { LazyDocumentPreview } from "../documents/LazyDocumentPreview";
import {
  closeAttachmentPreview,
  getAttachmentPreviewState,
  subscribeAttachmentPreview,
} from "./attachmentPreviewStore";
import {
  STORAGE_LIFECYCLE_EVENT,
  matchesStorageLifecycleEvent,
  type StorageLifecycleEventDetail,
} from "../../services/storageLifecycle";

export function AttachmentPreviewHost() {
  const [, forceRender] = useState(0);
  const previewStateRef = useRef(getAttachmentPreviewState());

  useEffect(() => {
    const syncPreviewState = () => {
      previewStateRef.current = getAttachmentPreviewState();
      forceRender((count) => count + 1);
    };

    return subscribeAttachmentPreview(syncPreviewState);
  }, []);

  const previewState = previewStateRef.current;
  const attachment = previewState?.attachment ?? null;

  useEffect(() => {
    if (!attachment) return;
    const closeDeletedPreview = (event: Event) => {
      const detail = (event as CustomEvent<StorageLifecycleEventDetail>).detail;
      if (
        matchesStorageLifecycleEvent(detail, attachment.fileId, attachment.key) &&
        (detail.status === "deleted" || detail.status === "delete_pending")
      ) {
        closeAttachmentPreview();
      }
    };
    window.addEventListener(STORAGE_LIFECYCLE_EVENT, closeDeletedPreview);
    return () => window.removeEventListener(STORAGE_LIFECYCLE_EVENT, closeDeletedPreview);
  }, [attachment]);

  return (
    <DelayedUnmount show={!!attachment}>
      {attachment && (
        <LazyDocumentPreview
          path={attachment.name}
          // Prefer the logical content URL: freshly uploaded managed files
          // still carry a physical key here, but the legacy /api/upload/file
          // endpoint deliberately 404s managed keys. Only server-projected
          // payloads blank the key, which is why previews worked only after
          // a page refresh. Legacy attachments without a URL keep the key path.
          s3Key={!attachment.url ? attachment.key : undefined}
          signedUrl={
            attachment.url ? getFullUrl(attachment.url) ?? attachment.url : undefined
          }
          fileSize={attachment.size}
          mimeType={attachment.mimeType}
          registryKey={`attachment-preview:${
            previewState?.source ?? "unknown"
          }:${attachment.fileId || attachment.key}`}
          imageUrl={
            attachment.type === "image" ? getFullUrl(attachment.url) : undefined
          }
          onClose={closeAttachmentPreview}
          mobileFillViewport
        />
      )}
    </DelayedUnmount>
  );
}
