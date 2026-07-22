import type { MessagePart } from "../../../types";

export interface SubagentPanelData {
  agentId: string;
  agentName: string;
  input: string;
  result?: string;
  success?: boolean;
  error?: string;
  isPending?: boolean;
  parts?: MessagePart[];
  startedAt?: number;
  completedAt?: number;
  status?: "pending" | "running" | "complete" | "error" | "cancelled";
}

type Listener = () => void;
type ScheduleNotification = (callback: () => void) => void;

const scheduleNotificationOnNextFrame: ScheduleNotification = (callback) => {
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => callback());
    return;
  }
  queueMicrotask(callback);
};

// Message parts follow the app's immutable-update contract. Reference equality
// avoids serializing an ever-growing streaming transcript on every token.
function shallowEqualPanelData(
  a: SubagentPanelData,
  b: SubagentPanelData,
): boolean {
  if (
    a.agentId !== b.agentId ||
    a.agentName !== b.agentName ||
    a.input !== b.input ||
    a.result !== b.result ||
    a.success !== b.success ||
    a.error !== b.error ||
    a.isPending !== b.isPending ||
    a.startedAt !== b.startedAt ||
    a.completedAt !== b.completedAt ||
    a.status !== b.status
  ) {
    return false;
  }
  return a.parts === b.parts;
}

export interface SubagentPanelStore {
  delete: (agentId: string) => void;
  get: (agentId: string) => SubagentPanelData | undefined;
  set: (data: SubagentPanelData) => void;
  size: () => number;
  subscribe: (agentId: string, listener: Listener) => () => void;
}

export function createSubagentPanelStore(
  scheduleNotification: ScheduleNotification = scheduleNotificationOnNextFrame,
): SubagentPanelStore {
  const data = new Map<string, SubagentPanelData>();
  const listeners = new Map<string, Set<Listener>>();
  const dirtyAgentIds = new Set<string>();
  let flushScheduled = false;

  function flush() {
    flushScheduled = false;
    const agentIds = [...dirtyAgentIds];
    dirtyAgentIds.clear();

    for (const agentId of agentIds) {
      const subscribed = listeners.get(agentId);
      if (!subscribed) continue;
      [...subscribed].forEach((listener) => listener());
    }
  }

  function scheduleEmit(agentId: string) {
    dirtyAgentIds.add(agentId);
    if (flushScheduled) return;
    flushScheduled = true;
    scheduleNotification(flush);
  }

  return {
    delete(agentId) {
      if (!data.delete(agentId)) {
        return;
      }
      scheduleEmit(agentId);
    },
    get(agentId) {
      return data.get(agentId);
    },
    set(next) {
      const prev = data.get(next.agentId);
      if (prev && shallowEqualPanelData(prev, next)) {
        return;
      }
      data.set(next.agentId, next);
      scheduleEmit(next.agentId);
    },
    size() {
      return data.size;
    },
    subscribe(agentId, listener) {
      const subscribed = listeners.get(agentId) ?? new Set<Listener>();
      subscribed.add(listener);
      listeners.set(agentId, subscribed);

      return () => {
        const current = listeners.get(agentId);
        if (!current) return;
        current.delete(listener);
        if (current.size === 0) {
          listeners.delete(agentId);
        }
      };
    },
  };
}

export const subagentPanelStore = createSubagentPanelStore();
