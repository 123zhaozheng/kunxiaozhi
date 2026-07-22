import assert from "node:assert/strict";
import test from "node:test";
import {
  createSubagentPanelStore as createScheduledSubagentPanelStore,
  type SubagentPanelData,
} from "../subagentPanelStore.ts";

const createSubagentPanelStore = () =>
  createScheduledSubagentPanelStore((callback) => callback());

function createManualScheduler() {
  const callbacks: Array<() => void> = [];
  return {
    schedule(callback: () => void) {
      callbacks.push(callback);
    },
    flushNext() {
      callbacks.shift()?.();
    },
    size() {
      return callbacks.length;
    },
  };
}

function createData(agentId: string): SubagentPanelData {
  return {
    agentId,
    agentName: `agent-${agentId}`,
    input: `input-${agentId}`,
    status: "running",
  };
}

test("notifies only listeners subscribed to the updated agent id", () => {
  const store = createSubagentPanelStore();
  const calls: string[] = [];

  store.subscribe("agent-a", () => calls.push("a"));
  store.subscribe("agent-b", () => calls.push("b"));

  store.set(createData("agent-a"));

  assert.deepEqual(calls, ["a"]);
});

test("notifies listeners when an agent entry is deleted", () => {
  const store = createSubagentPanelStore();
  const calls: string[] = [];

  store.set(createData("agent-a"));
  store.subscribe("agent-a", () => calls.push("a"));

  store.delete("agent-a");

  assert.deepEqual(calls, ["a"]);
  assert.equal(store.get("agent-a"), undefined);
});

test("tracks current store size for lightweight observability", () => {
  const store = createSubagentPanelStore();

  store.set(createData("agent-a"));
  store.set(createData("agent-b"));
  store.delete("agent-a");

  assert.equal(store.size(), 1);
});

// F1: set 脏检查 — 内容相同不 emit，任一字段变化才 emit

function createFullData(
  agentId: string,
  overrides: Partial<SubagentPanelData> = {},
): SubagentPanelData {
  return {
    agentId,
    agentName: `agent-${agentId}`,
    input: `input-${agentId}`,
    result: `result-${agentId}`,
    success: true,
    error: undefined,
    isPending: false,
    parts: [{ type: "text", content: "hello" }],
    startedAt: 1000,
    completedAt: 2000,
    status: "complete",
    ...overrides,
  };
}

test("set with identical field values does not notify listeners", () => {
  const store = createSubagentPanelStore();
  const calls: number[] = [];
  store.subscribe("agent-a", () => calls.push(calls.length + 1));

  const data = createFullData("agent-a");
  store.set(data);
  store.set({ ...data });

  assert.equal(calls.length, 1);
});

test("set notifies when any scalar field changes", () => {
  const store = createSubagentPanelStore();
  const calls: number[] = [];
  store.subscribe("agent-a", () => calls.push(calls.length + 1));

  const base = createFullData("agent-a");
  store.set(base);
  store.set({ ...base, status: "running" });
  store.set({ ...base, isPending: true });
  store.set({ ...base, result: "new-result" });
  store.set({ ...base, success: false });
  store.set({ ...base, error: "boom" });
  store.set({ ...base, startedAt: 1500 });
  store.set({ ...base, completedAt: 2500 });
  store.set({ ...base, input: "new-input" });
  store.set({ ...base, agentName: "renamed" });

  // 初始 set + 9 次字段变化 = 10 次 emit
  assert.equal(calls.length, 10);
});

test("set with same parts content but different array reference notifies", () => {
  const store = createSubagentPanelStore();
  const calls: number[] = [];
  store.subscribe("agent-a", () => calls.push(calls.length + 1));

  store.set(createFullData("agent-a"));
  // 新数组引用，内容相同
  store.set(
    createFullData("agent-a", {
      parts: [{ type: "text", content: "hello" }],
    }),
  );

  assert.equal(calls.length, 2);
});

test("set notifies when parts content changes", () => {
  const store = createSubagentPanelStore();
  const calls: number[] = [];
  store.subscribe("agent-a", () => calls.push(calls.length + 1));

  store.set(createFullData("agent-a"));
  store.set(
    createFullData("agent-a", {
      parts: [{ type: "text", content: "hello" }, { type: "text", content: "world" }],
    }),
  );
  store.set(
    createFullData("agent-a", {
      parts: [{ type: "text", content: "changed" }],
    }),
  );

  assert.equal(calls.length, 3);
});

test("set treats undefined parts and present parts as different", () => {
  const store = createSubagentPanelStore();
  const calls: number[] = [];
  store.subscribe("agent-a", () => calls.push(calls.length + 1));

  const dataWithoutParts = createFullData("agent-a", { parts: undefined });
  store.set(dataWithoutParts);
  store.set({ ...dataWithoutParts });

  assert.equal(calls.length, 1);

  store.set(createFullData("agent-a"));
  assert.equal(calls.length, 2);
});

test("first set for a new agent id always notifies", () => {
  const store = createSubagentPanelStore();
  const calls: number[] = [];
  store.subscribe("agent-a", () => calls.push(calls.length + 1));

  store.set(createFullData("agent-a"));

  assert.equal(calls.length, 1);
});

test("coalesces 100 writes for one agent into one scheduled notification", () => {
  const scheduler = createManualScheduler();
  const store = createScheduledSubagentPanelStore(scheduler.schedule);
  const snapshots: Array<SubagentPanelData | undefined> = [];
  store.subscribe("agent-a", () => snapshots.push(store.get("agent-a")));

  for (let index = 0; index < 100; index += 1) {
    store.set(
      createFullData("agent-a", {
        parts: [{ type: "text", content: `chunk-${index}` }],
      }),
    );
  }

  assert.equal(scheduler.size(), 1);
  assert.equal(snapshots.length, 0);
  const latestPart = store.get("agent-a")?.parts?.[0];
  assert.equal(
    latestPart?.type === "text" ? latestPart.content : undefined,
    "chunk-99",
  );

  scheduler.flushNext();

  assert.equal(snapshots.length, 1);
  assert.equal(snapshots[0], store.get("agent-a"));
});

test("coalesces different agents while keeping notifications isolated", () => {
  const scheduler = createManualScheduler();
  const store = createScheduledSubagentPanelStore(scheduler.schedule);
  const calls: string[] = [];
  store.subscribe("agent-a", () => calls.push("a"));
  store.subscribe("agent-b", () => calls.push("b"));

  store.set(createData("agent-a"));
  store.set(createData("agent-b"));
  store.set({ ...createData("agent-a"), status: "complete" });

  assert.equal(scheduler.size(), 1);
  scheduler.flushNext();
  assert.deepEqual(calls, ["a", "b"]);
});

test("set then delete in one frame exposes the deleted snapshot once", () => {
  const scheduler = createManualScheduler();
  const store = createScheduledSubagentPanelStore(scheduler.schedule);
  const snapshots: Array<SubagentPanelData | undefined> = [];
  store.subscribe("agent-a", () => snapshots.push(store.get("agent-a")));

  store.set(createData("agent-a"));
  store.delete("agent-a");
  scheduler.flushNext();

  assert.deepEqual(snapshots, [undefined]);
});

test("does not call a listener that unsubscribes before the frame flush", () => {
  const scheduler = createManualScheduler();
  const store = createScheduledSubagentPanelStore(scheduler.schedule);
  const calls: number[] = [];
  const unsubscribe = store.subscribe("agent-a", () => calls.push(1));

  store.set(createData("agent-a"));
  unsubscribe();
  scheduler.flushNext();

  assert.deepEqual(calls, []);
});

test("a listener write is deferred to the next scheduled flush", () => {
  const scheduler = createManualScheduler();
  const store = createScheduledSubagentPanelStore(scheduler.schedule);
  const snapshots: string[] = [];
  store.subscribe("agent-a", () => {
    const status = store.get("agent-a")?.status;
    snapshots.push(status ?? "missing");
    if (status === "running") {
      store.set({ ...createData("agent-a"), status: "complete" });
    }
  });

  store.set(createData("agent-a"));
  scheduler.flushNext();

  assert.deepEqual(snapshots, ["running"]);
  assert.equal(scheduler.size(), 1);

  scheduler.flushNext();
  assert.deepEqual(snapshots, ["running", "complete"]);
});
