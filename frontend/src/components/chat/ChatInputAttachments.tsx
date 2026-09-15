import { getFullUrl } from "../../services/api";
import { AttachmentCard } from "../common/AttachmentCard";
import { openAttachmentPreview } from "./attachmentPreviewStore";
import type { MessageAttachment } from "../../types";

interface ChatInputAttachmentsProps {
  attachments: MessageAttachment[];
  onAttachmentsChange: (
    attachments:
      | MessageAttachment[]
      | ((prev: MessageAttachment[]) => MessageAttachment[]),
  ) => void;
  onCancelUpload: (id: string) => void;
  onRetryUpload?: (id: string) => void;
  onImageViewerOpen: (url: string) => void;
}

export function ChatInputAttachments({
  attachments,
  onAttachmentsChange,
  onCancelUpload,
  onRetryUpload,
  onImageViewerOpen,
}: ChatInputAttachmentsProps) {
  if (attachments.length === 0) return null;

  return (
    <div className="mx-3 mt-2.5 -mb-1 flex gap-3 overflow-x-auto attachment-scroll pb-1">
      {attachments.map((attachment) => {
        const isImage =
          attachment.mimeType?.startsWith("image/") && attachment.url;

        const handleRemove = () => {
          if (attachment.uploadError) {
            onCancelUpload(attachment.id);
            return;
          }
          onAttachmentsChange((prev) =>
            prev.filter((a) => a.id !== attachment.id),
          );
        };

        return (
          <AttachmentCard
            key={attachment.id}
            attachment={attachment}
            variant="editable"
            size="compact"
            isUploading={attachment.isUploading}
            onClick={() => {
              if (isImage && attachment.url) {
                onImageViewerOpen(getFullUrl(attachment.url) ?? "");
              } else {
                openAttachmentPreview(attachment, "chat-input");
              }
            }}
            onRemove={handleRemove}
            onRetry={
              attachment.uploadError && onRetryUpload
                ? () => onRetryUpload(attachment.id)
                : undefined
            }
            onCancel={
              attachment.isUploading
                ? () => onCancelUpload(attachment.id)
                : undefined
            }
          />
        );
      })}
    </div>
  );
}
