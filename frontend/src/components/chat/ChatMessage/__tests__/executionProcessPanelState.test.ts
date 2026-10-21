import assert from "node:assert/strict";
import test from "node:test";
import {
  applyFailureAutoExpand,
  clearProcessGroupOverrides,
  getProcessGroupOverride,
  setProcessGroupExpanded,
  shouldAutoExpandForFailure,
  subscribeProcessGroupOverrides,
} from "../executionProcessPanelState.ts";

test.beforeEach(() => {
  clearProcessGroupOverrides();
});

test.afterEach(() => {
  clearProcessGroupOverrides();
});

test("unwritten message id returns undefined", () => {
  assert.equal(getProcessGroupOverride("never-written"), undefined);
});

test("written override is readable and switchable", () => {
  setProcessGroupExpanded("m1", true);
  assert.equal(getProcessGroupOverride("m1"), true);
  setProcessGroupExpanded("m1", false);
  assert.equal(getProcessGroupOverride("m1"), false);
  // 同一 store 按 messageId 隔离
  assert.equal(getProcessGroupOverride("m2"), undefined);
});

test("subscribers are notified on change, not on idempotent write", () => {
  const calls: string[] = [];
  const unsubscribe = subscribeProcessGroupOverrides(() => {
    calls.push("notify");
  });

  setProcessGroupExpanded("m1", true);
  assert.equal(calls.length, 1);
  // 同值重写不通知
  setProcessGroupExpanded("m1", true);
  assert.equal(calls.length, 1);
  // 切换值再次通知
  setProcessGroupExpanded("m1", false);
  assert.equal(calls.length, 2);

  unsubscribe();
  // 取消订阅后不再通知
  setProcessGroupExpanded("m1", true);
  assert.equal(calls.length, 2);
});

test("failure auto-expand decision matrix", () => {
  // 完成 + 有失败 + 未写过 → 展开
  assert.equal(
    shouldAutoExpandForFailure({ override: undefined, isStreaming: false, failedCount: 1 }),
    true,
  );
  // 流式中不写（分组默认展开已由 isStreaming 推导覆盖）
  assert.equal(
    shouldAutoExpandForFailure({ override: undefined, isStreaming: true, failedCount: 1 }),
    false,
  );
  // 无失败不写
  assert.equal(
    shouldAutoExpandForFailure({ override: undefined, isStreaming: false, failedCount: 0 }),
    false,
  );
  // 用户点过展开后不覆盖
  assert.equal(
    shouldAutoExpandForFailure({ override: true, isStreaming: false, failedCount: 2 }),
    false,
  );
  // 用户点过收起后不重新展开
  assert.equal(
    shouldAutoExpandForFailure({ override: false, isStreaming: false, failedCount: 2 }),
    false,
  );
});

test("applyFailureAutoExpand writes once and never overrides user choice", () => {
  // 首次：完成且有失败 → 写入 true
  assert.equal(applyFailureAutoExpand("m1", { isStreaming: false, failedCount: 1 }), true);
  assert.equal(getProcessGroupOverride("m1"), true);

  // 幂等：重复调用不再写
  assert.equal(applyFailureAutoExpand("m1", { isStreaming: false, failedCount: 1 }), false);

  // 用户手动收起后，自动逻辑不得重新展开
  setProcessGroupExpanded("m1", false);
  assert.equal(applyFailureAutoExpand("m1", { isStreaming: false, failedCount: 3 }), false);
  assert.equal(getProcessGroupOverride("m1"), false);
});

test("applyFailureAutoExpand does nothing while streaming or without failures", () => {
  assert.equal(applyFailureAutoExpand("s1", { isStreaming: true, failedCount: 1 }), false);
  assert.equal(getProcessGroupOverride("s1"), undefined);
  assert.equal(applyFailureAutoExpand("s2", { isStreaming: false, failedCount: 0 }), false);
  assert.equal(getProcessGroupOverride("s2"), undefined);
});
