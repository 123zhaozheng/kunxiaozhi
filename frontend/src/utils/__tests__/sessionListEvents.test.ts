import test from "node:test";
import assert from "node:assert/strict";
import {
  dispatchSessionListRefresh,
  subscribeSessionListRefresh,
} from "../sessionListEvents.ts";

test("notifies subscribers when the session list refresh is requested", () => {
  const target = new EventTarget();
  let calls = 0;

  const unsubscribe = subscribeSessionListRefresh(() => {
    calls += 1;
  }, target);

  dispatchSessionListRefresh(target);
  dispatchSessionListRefresh(target);
  assert.equal(calls, 2);

  unsubscribe();
  dispatchSessionListRefresh(target);
  assert.equal(calls, 2);
});

test("does not leak refresh events across event targets", () => {
  const first = new EventTarget();
  const second = new EventTarget();
  let calls = 0;

  const unsubscribe = subscribeSessionListRefresh(() => {
    calls += 1;
  }, first);

  dispatchSessionListRefresh(second);
  assert.equal(calls, 0);

  unsubscribe();
});
