import assert from "node:assert/strict";
import test from "node:test";

import {
  getForkMessageId,
  isSessionRunning,
  shouldShowStreamingFooterSkeleton,
} from "../sessionState.ts";

test("treats loading or visible streaming messages as an active session", () => {
  assert.equal(isSessionRunning([], true), true);
  assert.equal(
    isSessionRunning([{ isStreaming: false }, { isStreaming: true }], false),
    true,
  );
  assert.equal(isSessionRunning([{ isStreaming: false }], false), false);
});

test("shows the footer skeleton only when reconnecting after a stream disappears", () => {
  assert.equal(
    shouldShowStreamingFooterSkeleton({
      connectionStatus: "reconnecting",
      sessionRunning: true,
      messageCount: 2,
      hasVisibleStreamingMessage: false,
    }),
    true,
  );

  assert.equal(
    shouldShowStreamingFooterSkeleton({
      connectionStatus: "connected",
      sessionRunning: true,
      messageCount: 2,
      hasVisibleStreamingMessage: false,
    }),
    false,
  );

  assert.equal(
    shouldShowStreamingFooterSkeleton({
      connectionStatus: "disconnected",
      sessionRunning: true,
      messageCount: 2,
      hasVisibleStreamingMessage: true,
    }),
    false,
  );

  assert.equal(
    shouldShowStreamingFooterSkeleton({
      connectionStatus: "disconnected",
      sessionRunning: false,
      messageCount: 2,
      hasVisibleStreamingMessage: false,
    }),
    false,
  );
});

test("getForkMessageId sends the real run_id for assistant bubbles, even when suffixed", () => {
  // First bubble of a run: id === runId — both forms are valid
  assert.equal(
    getForkMessageId({ id: "run-1", role: "assistant", runId: "run-1" }),
    "run-1",
  );
  // Suffixed bubble from historyLoader's nextAssistantId (legacy pre-seq
  // events): id is "run-1:2" but the backend only knows "run-1"
  assert.equal(
    getForkMessageId({ id: "run-1:2", role: "assistant", runId: "run-1" }),
    "run-1",
  );
  // Defensive fallback if runId is missing
  assert.equal(
    getForkMessageId({ id: "msg-x", role: "assistant" }),
    "msg-x",
  );
});

test("getForkMessageId keeps the message id for user messages", () => {
  assert.equal(
    getForkMessageId({ id: "run-1:user", role: "user", runId: "run-1" }),
    "run-1:user",
  );
  assert.equal(getForkMessageId({ id: "msg-y", role: "user" }), "msg-y");
});
