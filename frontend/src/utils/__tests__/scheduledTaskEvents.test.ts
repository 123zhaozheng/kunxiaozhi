import test from "node:test";
import assert from "node:assert/strict";
import {
  SCHEDULED_TASK_REFRESH_EVENT,
  dispatchScheduledTaskRefresh,
  subscribeScheduledTaskRefresh,
} from "../scheduledTaskEvents.ts";

test("keeps the legacy event name", () => {
  assert.equal(SCHEDULED_TASK_REFRESH_EVENT, "scheduled-task:refresh");
});

test("notifies subscribers and stops after unsubscribe", () => {
  const target = new EventTarget();
  let calls = 0;

  const unsubscribe = subscribeScheduledTaskRefresh(() => {
    calls += 1;
  }, target);

  dispatchScheduledTaskRefresh(target);
  dispatchScheduledTaskRefresh(target);
  assert.equal(calls, 2);

  unsubscribe();
  dispatchScheduledTaskRefresh(target);
  assert.equal(calls, 2);
});

test("does not leak refresh events across event targets", () => {
  const first = new EventTarget();
  const second = new EventTarget();
  let calls = 0;

  const unsubscribe = subscribeScheduledTaskRefresh(() => {
    calls += 1;
  }, first);

  dispatchScheduledTaskRefresh(second);
  assert.equal(calls, 0);

  unsubscribe();
});

test("is a no-op without a window and a target", () => {
  assert.doesNotThrow(() => dispatchScheduledTaskRefresh());
  const unsubscribe = subscribeScheduledTaskRefresh(() => undefined);
  assert.doesNotThrow(() => unsubscribe());
});
