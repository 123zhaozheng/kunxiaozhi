import type { Message } from "../../types/message.ts";
import type { MessageAttachment } from "../../types/upload.ts";
import { uuid } from "../../utils/uuid.ts";

interface CreateOptimisticMessagesForSendOptions {
  previousMessages: Message[];
  content: string;
  attachments?: MessageAttachment[];
  now?: Date;
  createId?: () => string;
  /** Prefer the backend run id so SSE can attach without a rematch. */
  assistantMessageId?: string;
}

interface CreateOptimisticMessagesForSendResult {
  messages: Message[];
  assistantMessageId: string;
}

export function createOptimisticMessagesForSend({
  previousMessages,
  content,
  attachments,
  now = new Date(),
  createId = () => uuid(),
  assistantMessageId,
}: CreateOptimisticMessagesForSendOptions): CreateOptimisticMessagesForSendResult {
  const userMessage: Message = {
    id: createId(),
    role: "user",
    content: content.trim(),
    timestamp: now,
    attachments,
  };

  const resolvedAssistantId = assistantMessageId || createId();
  const assistantMessage: Message = {
    id: resolvedAssistantId,
    role: "assistant",
    content: "",
    timestamp: now,
    toolCalls: [],
    toolResults: [],
    isStreaming: true,
    ...(assistantMessageId ? { runId: assistantMessageId } : {}),
  };

  return {
    messages: [...previousMessages, userMessage, assistantMessage],
    assistantMessageId: resolvedAssistantId,
  };
}
