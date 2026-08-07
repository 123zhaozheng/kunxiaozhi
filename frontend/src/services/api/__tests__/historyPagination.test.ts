import test from "node:test";
import assert from "node:assert/strict";

import type { SessionEventsResponse, SSEEventRecord } from "../../../types/session";
import type { SharedContentResponse } from "../../../types/share";
import { sessionApi } from "../session";
import { shareApi } from "../share";

function sessionEvent(
  id: string,
  overrides: Partial<SSEEventRecord> = {},
): SSEEventRecord {
  return {
    id,
    event_type: "message",
    data: { id },
    timestamp: `2026-08-07T00:00:${id.padStart(2, "0")}Z`,
    ...overrides,
  };
}

function sessionPage(
  events: SSEEventRecord[],
  next_cursor: string | null = null,
  has_more = Boolean(next_cursor),
): SessionEventsResponse & { run_id?: string } {
  return {
    session_id: "session-1",
    events,
    has_more,
    next_cursor,
    history_complete: true,
  };
}

function sharedPage(
  events: SSEEventRecord[],
  next_cursor: string | null = null,
  has_more = Boolean(next_cursor),
): SharedContentResponse {
  return {
    session: { id: "session-1", agent_id: "agent-1" },
    owner: { username: "owner" },
    share_type: "full",
    events,
    has_more,
    next_cursor,
    history_complete: true,
  };
}

test("session history accumulates pages in order and deduplicates event identities", async () => {
  const original = sessionApi.getEvents;
  const calls: Array<{ after?: string; limit?: number }> = [];
  const duplicate = sessionEvent("2", { event_id: "event-2" });
  const pages: Record<string, SessionEventsResponse & { run_id?: string }> = {
    "": sessionPage([
      sessionEvent("1", { event_id: "event-1" }),
      duplicate,
    ], "cursor-1"),
    "cursor-1": sessionPage([
      duplicate,
      sessionEvent("3", { event_id: "event-3" }),
    ]),
  };

  sessionApi.getEvents = async (_sessionId, options) => {
    calls.push({ after: options?.after, limit: options?.limit });
    return pages[options?.after ?? ""];
  };
  try {
    const result = await sessionApi.getAllEvents("session-1", { limit: 2 });
    assert.deepEqual(result.events.map((event) => event.id), ["1", "2", "3"]);
    assert.deepEqual(calls, [
      { after: undefined, limit: 2 },
      { after: "cursor-1", limit: 2 },
    ]);
    assert.equal(result.has_more, false);
    assert.equal(result.next_cursor, null);
    assert.equal(result.history_complete, true);
  } finally {
    sessionApi.getEvents = original;
  }
});

test("session history stops on a repeated cursor and preserves loaded events as incomplete", async () => {
  const original = sessionApi.getEvents;
  let callCount = 0;
  const afterValues: Array<string | undefined> = [];
  sessionApi.getEvents = async (_sessionId, options) => {
    callCount += 1;
    afterValues.push(options?.after);
    return callCount === 1
      ? sessionPage([sessionEvent("1")], "same-cursor")
      : sessionPage([sessionEvent("2")], "same-cursor");
  };
  try {
    const result = await sessionApi.getAllEvents("session-1");
    assert.deepEqual(result.events.map((event) => event.id), ["1", "2"]);
    assert.equal(result.history_complete, false);
    assert.match(result.history_error ?? "", /invalid continuation cursor/);
    assert.equal(callCount, 2);
    assert.deepEqual(afterValues, [undefined, "same-cursor"]);
  } finally {
    sessionApi.getEvents = original;
  }
});

test("session history returns loaded pages when a later page fails", async () => {
  const original = sessionApi.getEvents;
  let callCount = 0;
  sessionApi.getEvents = async () => {
    callCount += 1;
    if (callCount === 1) return sessionPage([sessionEvent("1")], "cursor-1");
    throw new Error("backend unavailable");
  };
  try {
    const result = await sessionApi.getAllEvents("session-1");
    assert.deepEqual(result.events.map((event) => event.id), ["1"]);
    assert.equal(result.history_complete, false);
    assert.equal(result.history_error, "backend unavailable");
  } finally {
    sessionApi.getEvents = original;
  }
});

test("session history cancellation returns already loaded pages without an error", async () => {
  const original = sessionApi.getEvents;
  const controller = new AbortController();
  let callCount = 0;
  sessionApi.getEvents = async (_sessionId, options) => {
    callCount += 1;
    assert.equal(options?.signal, controller.signal);
    if (callCount === 1) {
      controller.abort();
      return sessionPage([sessionEvent("1")], "cursor-1");
    }
    assert.equal(options?.signal?.aborted, true);
    throw Object.assign(new Error("cancelled"), { name: "AbortError" });
  };
  try {
    const result = await sessionApi.getAllEvents("session-1", {
      signal: controller.signal,
    });
    assert.deepEqual(result.events.map((event) => event.id), ["1"]);
    assert.equal(result.history_complete, false);
    assert.equal(result.history_error, undefined);
  } finally {
    sessionApi.getEvents = original;
  }
});

test("shared history accumulates pages in order and deduplicates event identities", async () => {
  const original = shareApi.getSharedContent;
  const calls: Array<{ after?: string; limit?: number }> = [];
  const duplicate = sessionEvent("2", { event_id: "event-2" });
  const pages: Record<string, SharedContentResponse> = {
    "": sharedPage([
      sessionEvent("1", { event_id: "event-1" }),
      duplicate,
    ], "cursor-1"),
    "cursor-1": sharedPage([
      duplicate,
      sessionEvent("3", { event_id: "event-3" }),
    ]),
  };

  shareApi.getSharedContent = async (_shareId, options) => {
    calls.push({ after: options?.after, limit: options?.limit });
    return pages[options?.after ?? ""];
  };
  try {
    const result = await shareApi.getAllSharedContent("share-1", { limit: 2 });
    assert.deepEqual(result.events.map((event) => event.id), ["1", "2", "3"]);
    assert.deepEqual(calls, [
      { after: undefined, limit: 2 },
      { after: "cursor-1", limit: 2 },
    ]);
    assert.equal(result.has_more, false);
    assert.equal(result.next_cursor, null);
    assert.equal(result.history_complete, true);
  } finally {
    shareApi.getSharedContent = original;
  }
});

test("shared history stops on a repeated cursor and preserves loaded events as incomplete", async () => {
  const original = shareApi.getSharedContent;
  let callCount = 0;
  const afterValues: Array<string | undefined> = [];
  shareApi.getSharedContent = async (_shareId, options) => {
    callCount += 1;
    afterValues.push(options?.after);
    return callCount === 1
      ? sharedPage([sessionEvent("1")], "same-cursor")
      : sharedPage([sessionEvent("2")], "same-cursor");
  };
  try {
    const result = await shareApi.getAllSharedContent("share-1");
    assert.deepEqual(result.events.map((event) => event.id), ["1", "2"]);
    assert.equal(result.history_complete, false);
    assert.match(result.history_error ?? "", /invalid continuation cursor/);
    assert.equal(callCount, 2);
    assert.deepEqual(afterValues, [undefined, "same-cursor"]);
  } finally {
    shareApi.getSharedContent = original;
  }
});

test("shared history returns loaded pages when a later page fails", async () => {
  const original = shareApi.getSharedContent;
  let callCount = 0;
  shareApi.getSharedContent = async () => {
    callCount += 1;
    if (callCount === 1) return sharedPage([sessionEvent("1")], "cursor-1");
    throw new Error("share backend unavailable");
  };
  try {
    const result = await shareApi.getAllSharedContent("share-1");
    assert.deepEqual(result.events.map((event) => event.id), ["1"]);
    assert.equal(result.history_complete, false);
    assert.equal(result.history_error, "share backend unavailable");
  } finally {
    shareApi.getSharedContent = original;
  }
});

test("shared history cancellation returns already loaded pages without an error", async () => {
  const original = shareApi.getSharedContent;
  const controller = new AbortController();
  let callCount = 0;
  shareApi.getSharedContent = async (_shareId, options) => {
    callCount += 1;
    assert.equal(options?.signal, controller.signal);
    if (callCount === 1) {
      controller.abort();
      return sharedPage([sessionEvent("1")], "cursor-1");
    }
    assert.equal(options?.signal?.aborted, true);
    throw Object.assign(new Error("cancelled"), { name: "AbortError" });
  };
  try {
    const result = await shareApi.getAllSharedContent("share-1", {
      signal: controller.signal,
    });
    assert.deepEqual(result.events.map((event) => event.id), ["1"]);
    assert.equal(result.history_complete, false);
    assert.equal(result.history_error, undefined);
  } finally {
    shareApi.getSharedContent = original;
  }
});
