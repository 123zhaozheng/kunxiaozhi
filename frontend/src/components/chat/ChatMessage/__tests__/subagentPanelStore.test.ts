import assert from "node:assert/strict";
import test from "node:test";
import {
  createSubagentPanelStore,
  type SubagentPanelData,
} from "../subagentPanelStore.ts";

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

  store.set(createFullData("agent-a"));
  store.set(createFullData("agent-a"));

  assert.equal(calls.length, 1);
});

test("set notifies when any scalar field changes", () => {
  const store = createSubagentPanelStore();
  const calls: number[] = [];
  store.subscribe("agent-a", () => calls.push(calls.length + 1));

  store.set(createFullData("agent-a"));
  store.set(createFullData("agent-a", { status: "running" }));
  store.set(createFullData("agent-a", { isPending: true }));
  store.set(createFullData("agent-a", { result: "new-result" }));
  store.set(createFullData("agent-a", { success: false }));
  store.set(createFullData("agent-a", { error: "boom" }));
  store.set(createFullData("agent-a", { startedAt: 1500 }));
  store.set(createFullData("agent-a", { completedAt: 2500 }));
  store.set(createFullData("agent-a", { input: "new-input" }));
  store.set(createFullData("agent-a", { agentName: "renamed" }));

  // 初始 set + 9 次字段变化 = 10 次 emit
  assert.equal(calls.length, 10);
});

test("set with same parts content but different array reference does not notify", () => {
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

  assert.equal(calls.length, 1);
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

  store.set(createFullData("agent-a", { parts: undefined }));
  store.set(createFullData("agent-a", { parts: undefined }));

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
