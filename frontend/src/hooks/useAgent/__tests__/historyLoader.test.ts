import assert from "node:assert/strict";
import test from "node:test";

import { reconstructMessagesFromEvents } from "../historyLoader.ts";
import type { HistoryEvent } from "../types.ts";

test("reconstructMessagesFromEvents preserves backend user message ids", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        event_type: "user:message",
        run_id: "run-1",
        timestamp: "2026-05-08T00:00:00.000Z",
        data: {
          content: "fork from here",
          message_id: "user-message-1",
          attachments: [],
        },
      } satisfies HistoryEvent,
    ],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 1);
  assert.equal(messages[0]?.id, "user-message-1");
  assert.equal(messages[0]?.runId, "run-1");
});

test("reconstructMessagesFromEvents keeps legacy events before sequenced events", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        event_id: "new-event",
        event_type: "user:message",
        run_id: "run-new",
        seq: 1,
        timestamp: "2026-01-01T00:00:00.000Z",
        data: { content: "new", message_id: "new-user" },
      },
      {
        event_id: "legacy-event",
        event_type: "user:message",
        run_id: "run-legacy",
        timestamp: "2026-01-02T00:00:00.000Z",
        data: { content: "legacy", message_id: "legacy-user" },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.deepEqual(
    messages.filter((message) => message.role === "user").map((message) => message.id),
    ["legacy-user", "new-user"],
  );
});

test("reconstructMessagesFromEvents ignores goal update events as message content", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user",
        event_type: "user:message",
        run_id: "run-1",
        timestamp: "2026-05-08T00:00:00.000Z",
        data: {
          content: "/goal hi",
          message_id: "run-1:user",
          attachments: [],
        },
      },
      {
        id: "event-goal",
        event_type: "goal:updated",
        run_id: "run-1",
        timestamp: "2026-05-08T00:00:01.000Z",
        data: {
          action: "set",
          goal: { objective: "hi", rubric: "- greet" },
        },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 1);
  assert.equal(messages[0]?.role, "user");
});

test("reconstructMessagesFromEvents does not create duplicate assistant ids for goal lifecycle events", () => {
  const runId = "run_20260530120841_cf52eb51";
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user",
        event_type: "user:message",
        run_id: runId,
        timestamp: "2026-05-30T12:08:41.000Z",
        data: {
          content: "start",
          message_id: `${runId}:user`,
          attachments: [],
        },
      },
      {
        id: "event-thinking",
        event_type: "thinking",
        run_id: runId,
        timestamp: "2026-05-30T12:08:42.000Z",
        data: {
          content: "working",
        },
      },
      {
        id: "event-goal-start",
        event_type: "goal:start",
        run_id: runId,
        timestamp: "2026-05-30T12:08:43.000Z",
        data: {
          started_at: "2026-05-30T12:08:43.000Z",
          goal: { objective: "finish the task" },
        },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.deepEqual(
    messages.map((message) => message.id),
    [`${runId}:user`, runId],
  );
});

test("reconstructMessagesFromEvents ignores duplicate persisted user messages for the same run", () => {
  const runId = "run_20260530120841_cf52eb51";
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user-1",
        event_type: "user:message",
        run_id: runId,
        timestamp: "2026-05-30T12:08:41.000Z",
        data: {
          content: "hello",
          message_id: `${runId}:user`,
          attachments: [],
        },
      },
      {
        id: "event-thinking-1",
        event_type: "thinking",
        run_id: runId,
        timestamp: "2026-05-30T12:08:42.000Z",
        data: {
          content: "working",
        },
      },
      {
        id: "event-user-2",
        event_type: "user:message",
        run_id: runId,
        timestamp: "2026-05-30T12:08:43.000Z",
        data: {
          content: "hello",
          message_id: `${runId}:user`,
          attachments: [],
        },
      },
      {
        id: "event-thinking-2",
        event_type: "thinking",
        run_id: runId,
        timestamp: "2026-05-30T12:08:44.000Z",
        data: {
          content: " more",
        },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.deepEqual(
    messages.map((message) => message.id),
    [`${runId}:user`, runId],
  );
});

test("reconstructMessagesFromEvents ignores duplicate user messages with different ids for the same run", () => {
  const runId = "run_20260530120841_cf52eb51";
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user-1",
        event_type: "user:message",
        run_id: runId,
        timestamp: "2026-05-30T12:08:41.000Z",
        data: {
          content: "hello",
          message_id: "user-message-a",
          attachments: [],
        },
      },
      {
        id: "event-thinking-1",
        event_type: "thinking",
        run_id: runId,
        timestamp: "2026-05-30T12:08:42.000Z",
        data: {
          content: "working",
        },
      },
      {
        id: "event-user-2",
        event_type: "user:message",
        run_id: runId,
        timestamp: "2026-05-30T12:08:43.000Z",
        data: {
          content: "hello",
          message_id: "user-message-b",
          attachments: [],
        },
      },
      {
        id: "event-thinking-2",
        event_type: "thinking",
        run_id: runId,
        timestamp: "2026-05-30T12:08:44.000Z",
        data: {
          content: " more",
        },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.deepEqual(
    messages.map((message) => [message.id, message.role]),
    [
      ["user-message-a", "user"],
      [runId, "assistant"],
    ],
  );
});

test("reconstructMessagesFromEvents treats timezone-less backend timestamps as UTC", () => {
  const originalTimezone = process.env.TZ;
  process.env.TZ = "Asia/Shanghai";
  try {
    const messages = reconstructMessagesFromEvents(
      [
        {
          event_type: "user:message",
          run_id: "run-1",
          timestamp: "2026-05-07T16:30:00.000",
          data: {
            content: "hello",
            message_id: "user-message-1",
            attachments: [],
          },
        } satisfies HistoryEvent,
      ],
      new Set<string>(),
      { activeSubagentStack: [] },
    );

    assert.equal(
      messages[0]?.timestamp.toISOString(),
      "2026-05-07T16:30:00.000Z",
    );
  } finally {
    process.env.TZ = originalTimezone;
  }
});

test("reconstructMessagesFromEvents keeps token usage after cancel on the cancelled assistant", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user",
        event_type: "user:message",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:17.793Z",
        data: {
          content: "创建一个 Python Hello World 脚本",
          message_id: "run_20260516152217_bd0ba9a2:user",
          run_id: "run_20260516152217_bd0ba9a2",
          attachments: [],
        },
      },
      {
        id: "event-sandbox-starting",
        event_type: "sandbox:starting",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:18.961Z",
        data: {
          timestamp: "2026-05-16T15:22:18.961711+00:00",
          agent_id: "search",
        },
      },
      {
        id: "event-thinking",
        event_type: "thinking",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:40.515Z",
        data: {
          content:
            "用户要求创建一个 Python Hello World 脚本。这是一个简单的任务。",
          thinking_id: "lc_run--019e3161-c59c-7ab2-a91d-7249e2216feb",
          agent_id: "search",
        },
      },
      {
        id: "event-token-empty",
        event_type: "token:usage",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:43.422Z",
        data: {
          input_tokens: 0,
          output_tokens: 0,
          total_tokens: 0,
          duration: 0,
        },
      },
      {
        id: "event-cancel",
        event_type: "user:cancel",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:43.445Z",
        data: {
          run_id: "run_20260516152217_bd0ba9a2",
        },
      },
      {
        id: "event-token-final",
        event_type: "token:usage",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:43.732Z",
        data: {
          input_tokens: 15581,
          output_tokens: 68,
          total_tokens: 15649,
          duration: 24.927353858947754,
          model: "MiniMax-M2.7",
        },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 2);
  assert.equal(messages[0]?.role, "user");
  assert.equal(messages[1]?.role, "assistant");
  assert.equal(messages[1]?.cancelled, true);
  assert.equal(messages[1]?.tokenUsage?.total_tokens, 15649);
  assert.equal(messages[1]?.duration, 24927.353858947754);
});

test("reconstructMessagesFromEvents keeps late run events after cancel on the cancelled assistant", () => {
  const runId = "run_20260530120841_cf52eb51";
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user",
        event_type: "user:message",
        run_id: runId,
        timestamp: "2026-05-30T12:08:41.000Z",
        data: {
          content: "hello",
          message_id: `${runId}:user`,
          attachments: [],
        },
      },
      {
        id: "event-sandbox-ready",
        event_type: "sandbox:ready",
        run_id: runId,
        timestamp: "2026-05-30T12:08:42.000Z",
        data: {
          sandbox_id: "sandbox-1",
          work_dir: "/tmp/work",
        },
      },
      {
        id: "event-cancel",
        event_type: "user:cancel",
        run_id: runId,
        timestamp: "2026-05-30T12:08:43.000Z",
        data: {
          run_id: runId,
        },
      },
      {
        id: "event-thinking-late",
        event_type: "thinking",
        run_id: runId,
        timestamp: "2026-05-30T12:08:44.000Z",
        data: {
          content: "late thought",
        },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.deepEqual(
    messages.map((message) => message.id),
    [`${runId}:user`, runId],
  );
  assert.equal(messages[1]?.cancelled, true);
  assert.deepEqual(messages[1]?.parts?.map((part) => part.type), [
    "sandbox",
    "cancelled",
    "thinking",
  ]);
});

test("reconstructMessagesFromEvents does not duplicate an assistant id when a run's events are interleaved by another run's user message", () => {
  const runA = "run_20260618064521_a46159bd";
  const runB = "run_20260618064600_bbbbbbbb";
  const messages = reconstructMessagesFromEvents(
    [
      // run A: user message
      {
        id: "event-user-a",
        event_type: "user:message",
        run_id: runA,
        timestamp: "2026-06-18T06:45:21.000Z",
        data: { content: "question A", message_id: `${runA}:user`, attachments: [] },
      },
      // run A: first assistant chunk
      {
        id: "event-chunk-a1",
        event_type: "message:chunk",
        run_id: runA,
        timestamp: "2026-06-18T06:45:22.000Z",
        data: { content: "answer A part 1" },
      },
      // run B: user message — splits run A's events
      {
        id: "event-user-b",
        event_type: "user:message",
        run_id: runB,
        timestamp: "2026-06-18T06:46:00.000Z",
        data: { content: "question B", message_id: `${runB}:user`, attachments: [] },
      },
      // run A: second assistant chunk (arrives after run B's user message)
      {
        id: "event-chunk-a2",
        event_type: "message:chunk",
        run_id: runA,
        timestamp: "2026-06-18T06:46:30.000Z",
        data: { content: "answer A part 2" },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  const ids = messages.map((message) => message.id);
  // run A must produce exactly one assistant bubble (id = runA), not two.
  assert.equal(ids.filter((id) => id === runA).length, 1);
  // run A's chunks must both be folded into that single bubble.
  const assistantA = messages.find((message) => message.id === runA);
  assert.equal(assistantA?.role, "assistant");
  assert.equal(assistantA?.content, "answer A part 1answer A part 2");
});

test("reconstructMessagesFromEvents uses history_order for merged interleaving", () => {
  const runId = "run-merged";
  const traceId = "trace-merged";
  const messages = reconstructMessagesFromEvents(
    [
      {
        event_id: "event-text",
        event_type: "message:chunk",
        run_id: runId,
        // Missing seq is expected for merger-produced text rows.
        timestamp: "2026-08-11T00:00:06.000Z",
        history_order: ["2026-08-11T00:00:06.000Z", "2026-08-11T00:00:00.000Z", traceId, 5],
        data: { content: "final" },
      },
      {
        event_id: "event-thinking-3",
        event_type: "thinking",
        run_id: runId,
        timestamp: "2026-08-11T00:00:05.000Z",
        history_order: ["2026-08-11T00:00:05.000Z", "2026-08-11T00:00:00.000Z", traceId, 4],
        data: { content: "third", thinking_id: "thinking-3" },
      },
      {
        event_id: "event-tool-2",
        event_type: "tool:start",
        run_id: runId,
        seq: 22,
        timestamp: "2026-08-11T00:00:04.000Z",
        history_order: ["2026-08-11T00:00:04.000Z", "2026-08-11T00:00:00.000Z", traceId, 3],
        data: { tool: "second", tool_call_id: "tool-2", args: {} },
      },
      {
        event_id: "event-thinking-2",
        event_type: "thinking",
        run_id: runId,
        timestamp: "2026-08-11T00:00:03.000Z",
        history_order: ["2026-08-11T00:00:03.000Z", "2026-08-11T00:00:00.000Z", traceId, 2],
        data: { content: "second", thinking_id: "thinking-2" },
      },
      {
        event_id: "event-tool-1",
        event_type: "tool:start",
        run_id: runId,
        seq: 20,
        timestamp: "2026-08-11T00:00:02.000Z",
        history_order: ["2026-08-11T00:00:02.000Z", "2026-08-11T00:00:00.000Z", traceId, 1],
        data: { tool: "first", tool_call_id: "tool-1", args: {} },
      },
      {
        event_id: "event-thinking-1",
        event_type: "thinking",
        run_id: runId,
        timestamp: "2026-08-11T00:00:01.000Z",
        history_order: ["2026-08-11T00:00:01.000Z", "2026-08-11T00:00:00.000Z", traceId, 0],
        data: { content: "first", thinking_id: "thinking-1" },
      },
      {
        event_id: "event-user",
        event_type: "user:message",
        run_id: runId,
        seq: 1,
        timestamp: "2026-08-11T00:00:00.000Z",
        history_order: ["2026-08-11T00:00:00.000Z", "2026-08-11T00:00:00.000Z", traceId, 0],
        data: { content: "question", message_id: "user-merged" },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  const assistant = messages.find((message) => message.role === "assistant");
  assert.deepEqual(assistant?.parts?.map((part) => part.type), [
    "thinking",
    "tool",
    "thinking",
    "tool",
    "thinking",
    "text",
  ]);
  assert.deepEqual(
    assistant?.parts
      ?.filter((part) => part.type === "thinking")
      .map((part) => part.thinking_id),
    ["thinking-1", "thinking-2", "thinking-3"],
  );
});

test("reconstructMessagesFromEvents replays both completed turns", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        event_id: "turn-2-answer",
        event_type: "message:chunk",
        run_id: "run-2",
        seq: 4,
        timestamp: "2026-08-11T00:01:03.000Z",
        data: { content: "answer two" },
      },
      {
        event_id: "turn-1-user",
        event_type: "user:message",
        run_id: "run-1",
        seq: 1,
        timestamp: "2026-08-11T00:01:00.000Z",
        data: { content: "question one", message_id: "user-1" },
      },
      {
        event_id: "turn-2-user",
        event_type: "user:message",
        run_id: "run-2",
        seq: 3,
        timestamp: "2026-08-11T00:01:02.000Z",
        data: { content: "question two", message_id: "user-2" },
      },
      {
        event_id: "turn-1-answer",
        event_type: "message:chunk",
        run_id: "run-1",
        seq: 2,
        timestamp: "2026-08-11T00:01:01.000Z",
        data: { content: "answer one" },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.deepEqual(
    messages.map((message) => [message.role, message.content]),
    [
      ["user", "question one"],
      ["assistant", "answer one"],
      ["user", "question two"],
      ["assistant", "answer two"],
    ],
  );
});
