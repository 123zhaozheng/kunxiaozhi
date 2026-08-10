/**
 * History event loader for useAgent hook
 * Reconstructs messages from stored events.
 *
 * Message transformation logic is unified in processMessageEvent (messageParts.ts).
 * This file handles: event iteration, message reconstruction, and
 * user:message / user:cancel / approval_required which are history-specific.
 */

import type { Message, MessagePart, FormField } from "../../types";
import { uuid } from "../../utils/uuid";
import { authFetch } from "../../services/api/fetch";
import { buildApiUrl } from "../../services/api/config";
import i18n from "../../i18n";
import type {
  EventData,
  SubagentStackItem,
  HistoryEvent,
  HistoryEventData,
  ActiveGoalSpec,
} from "./types";
import { convertAttachments, processMessageEvent } from "./eventProcessor";
import { clearAllLoadingStates } from "./messageParts";
import { parseDate } from "../../utils/datetime";

function resolveUserMessageId(
  event: HistoryEvent,
  eventData: HistoryEventData,
): string {
  if (typeof eventData.message_id === "string" && eventData.message_id.trim()) {
    return eventData.message_id;
  }
  if (typeof event.run_id === "string" && event.run_id.trim()) {
    return `${event.run_id}:user`;
  }
  return uuid();
}

interface ProcessHistoryOptions {
  options?: {
    onApprovalRequired?: (approval: {
      id: string;
      message: string;
      type: string;
      fields?: FormField[];
    }) => void;
  };
  activeSubagentStack: SubagentStackItem[];
}

function parseEventTimestamp(
  timestamp: string | undefined,
  fallbackMs: number,
): Date {
  return timestamp ? parseDate(timestamp) : new Date(fallbackMs);
}

function canAttachEventTypeToPreviousAssistant(eventType: string): boolean {
  return (
    eventType !== "user:message" &&
    eventType !== "user:cancel" &&
    eventType !== "metadata" &&
    eventType !== "done" &&
    eventType !== "goal:updated" &&
    eventType !== "approval_required" &&
    // SOP plans have their own DAG card rebuilt from `sopPlan` state — do not
    // rehydrate them into message bodies.
    eventType !== "sop:updated"
  );
}

function canAttachToPreviousAssistant(
  event: HistoryEvent,
  message: Message | undefined,
): message is Message {
  return (
    message?.role === "assistant" &&
    Boolean(event.run_id) &&
    message.runId === event.run_id
  );
}

/**
 * Process a single history event and update message state.
 * Returns updated currentAssistantMessage or new message.
 */
function processHistoryEvent(
  event: HistoryEvent,
  currentAssistantMessage: Message | null,
  processedEventIds: Set<string>,
  opts: ProcessHistoryOptions,
): Message | null {
  const eventType = event.event_type;
  const eventData = event.data as HistoryEventData;
  const depth = eventData.depth || 0;
  const agentId = eventData.agent_id;

  // Track processed event IDs
  if (event.id) {
    processedEventIds.add(event.id.toString());
  }

  // Handle user message
  if (eventType === "user:message") {
    return null; // Signal to push current assistant and create user message
  }

  // Skip events that don't contribute to message content
  if (
    eventType === "metadata" ||
    eventType === "done" ||
    eventType === "goal:updated" ||
    // SOP plans are rebuilt from the latest sop:updated snapshot into the
    // `sopPlan` state — they are not part of a message body.
    eventType === "sop:updated"
  ) {
    return currentAssistantMessage;
  }

  // Handle approval_required
  if (eventType === "approval_required") {
    const approvalData = eventData as {
      id?: string;
      message?: string;
      type?: string;
      approval_type?: string;
      plan?: Record<string, unknown>;
      fields?: FormField[];
    };
    // SOP plans have their own structured DAG card. Do not rehydrate them into
    // the generic approval list on history loads.
    if (
      approvalData.approval_type === "sop_plan" ||
      approvalData.type === "sop_plan"
    ) {
      return currentAssistantMessage;
    }
    if (approvalData.id && opts.options?.onApprovalRequired) {
      authFetch<{
        status: string;
        message?: string;
        type?: string;
        fields?: FormField[];
      }>(buildApiUrl(`/human/${approvalData.id}`))
        .then((data) => data ?? null)
        .then((approval) => {
          if (approval?.status === "pending") {
            opts.options?.onApprovalRequired?.({
              id: approvalData.id!,
              message: approval.message || "",
              type: approval.type || "form",
              fields: approval.fields,
            });
          }
        })
        .catch((e) => {
          console.warn("[loadHistory] Failed to check approval status:", e);
        });
    }
    return currentAssistantMessage;
  }

  // CancelledError with no current message — don't create an empty assistant message
  if (eventType === "error") {
    const errorData = eventData as { type?: string };
    if (errorData.type === "CancelledError" && !currentAssistantMessage) {
      return null;
    }
  }

  // Ensure assistant message exists for other event types
  let msg = currentAssistantMessage;
  if (!msg) {
    const messageId = event.run_id || uuid();
    msg = {
      id: messageId,
      role: "assistant",
      content: "",
      timestamp: parseEventTimestamp(event.timestamp, Date.now()),
      parts: [],
      isStreaming: false,
      runId: event.run_id,
    };
  } else if (event.run_id && !msg.runId) {
    msg = { ...msg, runId: event.run_id };
  }

  // Manage subagent stack
  if (eventType === "agent:call") {
    opts.activeSubagentStack.push({
      agent_id: agentId || "unknown",
      depth,
      message_id: msg.id,
    });
  }

  // Use unified event processor
  const result = processMessageEvent(
    eventType,
    eventData as EventData,
    msg.parts || [],
    msg.content,
    msg.toolCalls || [],
    depth,
    opts.activeSubagentStack,
    false, // isStreaming = false for history
    msg.id,
  );

  // Apply result to message
  msg.parts = result.parts;
  msg.content = result.content;
  msg.toolCalls = result.toolCalls;

  if (result.toolResult) {
    msg.toolResults = [...(msg.toolResults || []), result.toolResult];
  }
  if (result.tokenUsage) {
    msg.tokenUsage = result.tokenUsage;
  }
  if (result.duration) {
    msg.duration = result.duration;
  }
  if (result.cancelled) {
    msg.cancelled = true;
  }

  // Pop subagent stack after agent:result
  if (eventType === "agent:result") {
    const stackIndex = opts.activeSubagentStack.findIndex(
      (item) =>
        item.agent_id === (agentId || "unknown") && item.message_id === msg.id,
    );
    if (stackIndex !== -1) {
      opts.activeSubagentStack.splice(stackIndex, 1);
    }
  }

  return msg;
}

/**
 * Reconstruct messages from history events.
 */
export function reconstructMessagesFromEvents(
  events: HistoryEvent[],
  processedEventIds: Set<string>,
  opts: ProcessHistoryOptions,
): Message[] {
  // Sort events into a stable global order. Prefer the backend's session-level
  // `seq` (monotonic across all traces) — it expresses causal write order and
  // is immune to same-millisecond timestamp ties that made the old
  // timestamp-only sort reorder a run's events and spawn duplicate assistant
  // ids. Fall back to timestamp for legacy events written before seq existed.
  // Match the backend composite order. In particular, do not fall back to
  // timestamp when only one side has a sequence: merge mode can contain both
  // legacy and immutable events, and that fallback reorders the page.
  const sortedEvents = [...events].sort((a, b) => {
    const seqA = typeof a.seq === "number" ? a.seq : null;
    const seqB = typeof b.seq === "number" ? b.seq : null;
    if ((seqA !== null) !== (seqB !== null)) return seqA === null ? -1 : 1;
    if (seqA !== null && seqB !== null && seqA !== seqB) return seqA - seqB;

    const timeA = a.timestamp || "";
    const timeB = b.timestamp || "";
    if (timeA !== timeB) return timeA < timeB ? -1 : 1;

    const traceA = a.trace_id || "";
    const traceB = b.trace_id || "";
    if (traceA !== traceB) return traceA < traceB ? -1 : 1;

    const eventA = String(a.event_id ?? a.id ?? "");
    const eventB = String(b.event_id ?? b.id ?? "");
    if (eventA !== eventB) return eventA < eventB ? -1 : 1;
    return 0;
  });

  const reconstructedMessages: Message[] = [];
  let currentAssistantMessage: Message | null = null;
  const seenUserMessageIds = new Set<string>();
  const seenUserMessageRunIds = new Set<string>();
  // Map from run_id to the index (in reconstructedMessages) of the currently
  // open assistant bubble for that run. Ensures a run's split events reattach
  // to the same bubble instead of spawning a second message with the same id.
  //
  // Without this, a run's events can be split by an interleaving user:message
  // from another run (or by timestamp reordering), producing a second assistant
  // message with id = run_id. Virtuoso's computeItemKey uses message.id, so a
  // duplicate id makes React reconciliation render the same bubble across the
  // whole viewport on scroll (the "screen fills with one AI reply" bug).
  const assistantMessageIndexByRunId = new Map<string, number>();
  // How many assistant bubbles have been created for a given run_id. The first
  // bubble keeps id = run_id (matching the live send path, where the optimistic
  // assistant id is replaced with run_id). A later bubble — only ever created
  // when a run's events are so reordered that the first bubble can't be
  // reattached — gets a suffixed id (`${run_id}:2`, ...) so it never collides.
  const assistantBubbleCountByRunId = new Map<string, number>();

  const runIdOf = (event: HistoryEvent): string | null => {
    const r = event.run_id;
    return typeof r === "string" && r.trim() ? r : null;
  };

  // Build the id for a new assistant bubble of `runId`. The first bubble of a
  // run uses the run_id verbatim (preserving the live-path id contract); a
  // later bubble gets a `:N` suffix to stay unique.
  const nextAssistantId = (runId: string | null): string => {
    if (!runId) return uuid();
    const count = (assistantBubbleCountByRunId.get(runId) ?? 0) + 1;
    assistantBubbleCountByRunId.set(runId, count);
    return count === 1 ? runId : `${runId}:${count}`;
  };

  // Push the in-progress assistant bubble into the array and record its run_id
  // so later split events from the same run can reattach to it.
  const parkCurrentAssistant = () => {
    if (!currentAssistantMessage) return;
    reconstructedMessages.push(currentAssistantMessage);
    if (currentAssistantMessage.runId) {
      assistantMessageIndexByRunId.set(
        currentAssistantMessage.runId,
        reconstructedMessages.length - 1,
      );
    }
    currentAssistantMessage = null;
  };

  for (const event of sortedEvents) {
    const eventType = event.event_type;
    const eventData = event.data as HistoryEventData;

    // Handle user message separately
    if (eventType === "user:message") {
      const userMessageId = resolveUserMessageId(event, eventData);
      const userMessageRunId = runIdOf(event);
      if (
        seenUserMessageIds.has(userMessageId) ||
        (userMessageRunId && seenUserMessageRunIds.has(userMessageRunId))
      ) {
        continue;
      }
      seenUserMessageIds.add(userMessageId);
      if (userMessageRunId) {
        seenUserMessageRunIds.add(userMessageRunId);
      }

      parkCurrentAssistant();
      const userAttachments = convertAttachments(eventData.attachments);
      reconstructedMessages.push({
        id: userMessageId,
        role: "user",
        content: eventData.content || "",
        timestamp: parseEventTimestamp(event.timestamp, Date.now()),
        attachments: userAttachments,
        runId: event.run_id,
      });
      continue;
    }

    // Handle user cancel
    if (eventType === "user:cancel") {
      if (currentAssistantMessage) {
        const clearedParts = clearAllLoadingStates(
          currentAssistantMessage.parts || [],
        );
        // Also set result on pending tools for history display
        const updatedParts = clearedParts.map((part): MessagePart => {
          if (part.type === "tool" && part.cancelled && !part.result) {
            return {
              ...part,
              result: i18n.t("chat.cancelled"),
              success: false,
            };
          }
          return part;
        });
        currentAssistantMessage = {
          ...currentAssistantMessage,
          isStreaming: false,
          cancelled: true,
          parts: [...updatedParts, { type: "cancelled" as const }],
        };
        parkCurrentAssistant();
      } else {
        reconstructedMessages.push({
          id: uuid(),
          role: "assistant",
          content: "",
          timestamp: parseEventTimestamp(event.timestamp, Date.now()),
          parts: [{ type: "cancelled" }],
          runId: event.run_id,
        });
      }
      currentAssistantMessage = null;
      continue;
    }

    if (!currentAssistantMessage) {
      const runId = runIdOf(event);

      // Reuse this run's existing assistant bubble if one was parked earlier
      // because its events got split by an interleaving user message from
      // another run (or by timestamp reordering). Late events arriving after a
      // cancel also reattach here — they belong to the cancelled bubble (e.g.
      // token usage, late thinking). This is the fix for duplicate assistant
      // ids: instead of spawning a second message with id = run_id, we splice
      // the parked bubble back out and continue accumulating into it.
      if (runId && assistantMessageIndexByRunId.has(runId)) {
        const parkedIndex = assistantMessageIndexByRunId.get(runId)!;
        const parked = reconstructedMessages[parkedIndex];
        if (parked && parked.role === "assistant") {
          reconstructedMessages.splice(parkedIndex, 1);
          currentAssistantMessage = parked;
          assistantMessageIndexByRunId.delete(runId);
          for (const [otherRunId, idx] of assistantMessageIndexByRunId) {
            if (idx > parkedIndex) {
              assistantMessageIndexByRunId.set(otherRunId, idx - 1);
            }
          }
          currentAssistantMessage = processHistoryEvent(
            event,
            currentAssistantMessage,
            processedEventIds,
            opts,
          );
          continue;
        }
      }

      // Fall back to attaching to the previous assistant message when it shares
      // the same run_id (preserves original behavior for contiguous runs where
      // the bubble is the last parked element).
      if (canAttachEventTypeToPreviousAssistant(eventType)) {
        const lastMessageIndex = reconstructedMessages.length - 1;
        const lastMessage = reconstructedMessages[lastMessageIndex];
        if (canAttachToPreviousAssistant(event, lastMessage)) {
          reconstructedMessages.splice(lastMessageIndex, 1);
          if (lastMessage.runId) {
            assistantMessageIndexByRunId.delete(lastMessage.runId);
          }
          currentAssistantMessage = processHistoryEvent(
            event,
            lastMessage,
            processedEventIds,
            opts,
          );
          continue;
        }
      }

      // Create a new assistant bubble for this run. The first bubble of a run
      // uses id = run_id (matching the live send path); any later bubble gets a
      // `:N` suffix so it can never collide with the first.
      const newId = nextAssistantId(runId);
      currentAssistantMessage = processHistoryEvent(
        event,
        null,
        processedEventIds,
        opts,
      );
      if (currentAssistantMessage) {
        // processHistoryEvent sets id = event.run_id; override with our unique
        // id so a run can never produce two messages with the same id.
        currentAssistantMessage = { ...currentAssistantMessage, id: newId };
      }
      continue;
    }

    // Accumulate into the in-progress assistant bubble.
    currentAssistantMessage = processHistoryEvent(
      event,
      currentAssistantMessage,
      processedEventIds,
      opts,
    );
  }

  parkCurrentAssistant();

  return reconstructedMessages;
}

export interface RunningAssistantPreparationResult {
  messages: Message[];
  streamingMessageId: string;
}

export function prepareMessagesForRunningRun(
  messages: Message[],
  runId: string,
  createId: () => string = () => uuid(),
): RunningAssistantPreparationResult {
  const existingAssistant = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && message.runId === runId);

  if (existingAssistant) {
    return {
      streamingMessageId: existingAssistant.id,
      messages: messages.map((message) =>
        message.id === existingAssistant.id
          ? { ...message, isStreaming: true }
          : message,
      ),
    };
  }

  const streamingMessageId = createId();
  return {
    streamingMessageId,
    messages: [
      ...messages,
      {
        id: streamingMessageId,
        role: "assistant",
        content: "",
        timestamp: new Date(),
        parts: [],
        isStreaming: true,
        runId,
      },
    ],
  };
}

/**
 * Get the last event timestamp from sorted events.
 */
export function getLastEventTimestamp(events: HistoryEvent[]): Date | null {
  if (events.length === 0) return null;
  let lastEvent: HistoryEvent | null = null;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].timestamp) {
      lastEvent = events[i];
      break;
    }
  }
  return lastEvent?.timestamp ? parseDate(lastEvent.timestamp) : null;
}

/**
 * Extract the latest active goal from history events.
 *
 * Scans for the most recent `goal:start` / `goal:end` pair and reconstructs
 * an `ActiveGoalSpec` so the UI can show the goal indicator after a page
 * reload or session switch.
 */
export function extractGoalFromEvents(
  events: HistoryEvent[],
): ActiveGoalSpec | null {
  let goal: ActiveGoalSpec | null = null;

  for (const event of events) {
    const eventType = event.event_type;
    if (eventType !== "goal:start" && eventType !== "goal:end") continue;

    const data = event.data as Record<string, unknown> | null | undefined;
    if (!data) continue;

    const goalData = data.goal as Record<string, unknown> | undefined;
    const existing: ActiveGoalSpec = goal ?? {
      objective: "",
    };

    const next: ActiveGoalSpec = {
      objective: (goalData?.objective as string) ?? existing.objective ?? "",
      rubric: (goalData?.rubric as string) ?? existing.rubric,
      started_at: (data.started_at as string) ?? existing.started_at,
    };
    if (event.run_id) next.runId = event.run_id;
    else if (existing.runId) next.runId = existing.runId;
    if (goalData?.max_iterations != null)
      next.max_iterations = goalData.max_iterations as number;
    else if (existing.max_iterations != null)
      next.max_iterations = existing.max_iterations;

    if (eventType === "goal:end") {
      next.ended_at = (data.ended_at as string) ?? undefined;
    }

    goal = next;
  }

  // Don't restore completed goals — only show the bar for still-active ones.
  if (!goal || !goal.objective || goal.ended_at) return null;
  return goal;
}

export function extractGoalsByRunFromEvents(
  events: HistoryEvent[],
): Record<string, ActiveGoalSpec> {
  const goalsByRunId: Record<string, ActiveGoalSpec> = {};

  for (const event of events) {
    const eventType = event.event_type;
    if (eventType !== "goal:start" && eventType !== "goal:end") continue;
    if (!event.run_id) continue;

    const data = event.data as Record<string, unknown> | null | undefined;
    if (!data) continue;

    const goalData = data.goal as Record<string, unknown> | undefined;
    const existing: ActiveGoalSpec = goalsByRunId[event.run_id] ?? {
      objective: "",
      runId: event.run_id,
    };

    const next: ActiveGoalSpec = {
      objective: (goalData?.objective as string) ?? existing.objective ?? "",
      rubric: (goalData?.rubric as string) ?? existing.rubric,
      runId: event.run_id,
      started_at: (data.started_at as string) ?? existing.started_at,
    };
    if (goalData?.max_iterations != null)
      next.max_iterations = goalData.max_iterations as number;
    else if (existing.max_iterations != null)
      next.max_iterations = existing.max_iterations;

    if (eventType === "goal:end") {
      next.ended_at = (data.ended_at as string) ?? existing.ended_at;
    } else if (existing.ended_at) {
      next.ended_at = existing.ended_at;
    }

    if (next.objective) {
      goalsByRunId[event.run_id] = next;
    }
  }

  return goalsByRunId;
}
