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

// 脏检查：内容相同时跳过 set 与 emit，避免高频 SSE 事件引发渲染循环。
// parts 每次 SSE 事件都是新引用，故用 JSON.stringify 比较内容；其余字段用 ===。
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
  if (a.parts === b.parts) return true;
  if (!a.parts || !b.parts) return false;
  return JSON.stringify(a.parts) === JSON.stringify(b.parts);
}

export interface SubagentPanelStore {
  delete: (agentId: string) => void;
  get: (agentId: string) => SubagentPanelData | undefined;
  set: (data: SubagentPanelData) => void;
  size: () => number;
  subscribe: (agentId: string, listener: Listener) => () => void;
}

export function createSubagentPanelStore(): SubagentPanelStore {
  const data = new Map<string, SubagentPanelData>();
  const listeners = new Map<string, Set<Listener>>();

  function emit(agentId: string) {
    const subscribed = listeners.get(agentId);
    if (!subscribed) return;
    subscribed.forEach((listener) => listener());
  }

  return {
    delete(agentId) {
      if (!data.delete(agentId)) {
        return;
      }
      emit(agentId);
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
      emit(next.agentId);
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
