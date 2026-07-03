import type { Message } from "../../../types";
import type { SessionConfig } from "../../../hooks/useAgent/types";
import type { ConnectionStatus } from "../../../types";

export function isSessionRunning(
  messages: Pick<Message, "isStreaming">[],
  isLoading: boolean,
): boolean {
  return isLoading || messages.some((message) => message.isStreaming);
}

export function shouldShowStreamingFooterSkeleton({
  connectionStatus,
  sessionRunning,
  messageCount,
  hasVisibleStreamingMessage,
}: {
  connectionStatus?: ConnectionStatus;
  sessionRunning: boolean;
  messageCount: number;
  hasVisibleStreamingMessage: boolean;
}): boolean {
  const lostStream =
    connectionStatus === "disconnected" || connectionStatus === "reconnecting";

  return (
    lostStream &&
    sessionRunning &&
    messageCount > 0 &&
    !hasVisibleStreamingMessage
  );
}

export function getRestoredModelSelection(
  config: Pick<SessionConfig, "agent_options">,
): {
  modelId: string;
  modelValue: string;
} {
  const modelId =
    typeof config.agent_options?.model_id === "string"
      ? config.agent_options.model_id
      : "";
  const modelValue =
    typeof config.agent_options?.model === "string"
      ? config.agent_options.model
      : "";

  return {
    modelId,
    modelValue,
  };
}

/**
 * Resolve the message id to send to the backend fork endpoint.
 *
 * Reconstructed assistant bubbles can carry a `:N` suffix (see
 * `nextAssistantId` in historyLoader) when a run's events get split during
 * history reconstruction — common for legacy events written before `seq`
 * existed, which fall back to timestamp sorting and tie-break apart a run.
 * The backend `_resolve_fork_target` only recognizes the real run_id, so for
 * assistant messages we must send `runId` (which is always the real run_id),
 * not the suffixed bubble `id`. User message ids are already backend-recognized
 * (`message_id` or `runId:user`), so they pass through unchanged.
 */
export function getForkMessageId(
  message: Pick<Message, "id" | "role" | "runId">,
): string {
  if (message.role === "assistant") {
    return message.runId ?? message.id;
  }
  return message.id;
}
