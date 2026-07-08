import assert from "node:assert/strict";
import test from "node:test";
import {
  closePersistentToolPanel,
  getPersistentToolPanelState,
  openPersistentToolPanel,
  subscribePersistentToolPanel,
  updatePersistentToolPanel,
} from "../persistentToolPanelState.tsx";

test("keyed panel updates do not replace another open panel", () => {
  closePersistentToolPanel();
  openPersistentToolPanel({
    title: "Tool result",
    status: "success",
    children: "tool body",
    panelKey: "tool:1",
  });

  updatePersistentToolPanel(
    (prev) => ({
      ...prev,
      title: "Summary",
      children: "summary body",
    }),
    "summary:1",
  );

  assert.equal(getPersistentToolPanelState()?.title, "Tool result");
  assert.equal(getPersistentToolPanelState()?.children, "tool body");

  closePersistentToolPanel();
});

test("same-reference panel update does not notify listeners", () => {
  closePersistentToolPanel();
  const calls: number[] = [];
  const unsubscribe = subscribePersistentToolPanel(() => {
    calls.push(calls.length + 1);
  });

  openPersistentToolPanel({
    title: "Tool result",
    status: "success",
    children: "tool body",
    panelKey: "tool:1",
  });
  const before = getPersistentToolPanelState();

  updatePersistentToolPanel((prev) => prev, "tool:1");

  assert.equal(getPersistentToolPanelState(), before);
  assert.equal(calls.length, 1);

  unsubscribe();
  closePersistentToolPanel();
});

// F4: updater 仅在 status/subtitle 变化时返回新对象；
// 未变时返回 prev，createSingletonStore 的 Object.is 判断相同不 emit。
test("updater returning prev when status and subtitle unchanged does not notify", () => {
  closePersistentToolPanel();
  const calls: number[] = [];
  const unsubscribe = subscribePersistentToolPanel(() => {
    calls.push(calls.length + 1);
  });

  openPersistentToolPanel({
    title: "Tool result",
    status: "success",
    subtitle: "2026-01-01",
    children: "tool body",
    panelKey: "tool:1",
  });
  const before = getPersistentToolPanelState();

  // 模拟 SubagentBlock effect 内 F4 updater：字段未变时返回 prev
  updatePersistentToolPanel(
    (prev) =>
      prev.status === "success" && prev.subtitle === "2026-01-01"
        ? prev
        : { ...prev, status: "success", subtitle: "2026-01-01" },
    "tool:1",
  );

  assert.equal(getPersistentToolPanelState(), before);
  assert.equal(calls.length, 1);

  unsubscribe();
  closePersistentToolPanel();
});

test("updater returning new object when status changed does notify", () => {
  closePersistentToolPanel();
  const calls: number[] = [];
  const unsubscribe = subscribePersistentToolPanel(() => {
    calls.push(calls.length + 1);
  });

  openPersistentToolPanel({
    title: "Tool result",
    status: "loading",
    subtitle: "2026-01-01",
    children: "tool body",
    panelKey: "tool:1",
  });

  // 状态从 loading 变 success → 返回新对象 → emit
  updatePersistentToolPanel(
    (prev) =>
      prev.status === "success" && prev.subtitle === "2026-01-01"
        ? prev
        : { ...prev, status: "success", subtitle: "2026-01-01" },
    "tool:1",
  );

  assert.equal(getPersistentToolPanelState()?.status, "success");
  assert.equal(calls.length, 2);

  unsubscribe();
  closePersistentToolPanel();
});
