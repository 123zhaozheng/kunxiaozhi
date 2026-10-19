import assert from "node:assert/strict";
import test from "node:test";
import {
  classifyGenericToolKind,
  computeStepDurationMs,
  countFailedSteps,
  countProcessSteps,
  getCurrentActionSummary,
  getEntryCapability,
  getStepStatus,
  getStepSummary,
  isProcessEntryFinished,
  isSpreadsheetStep,
  makeProcessSegmentKey,
  MAX_CAPABILITY_ICONS,
  partitionMessageParts,
  partitionProcessSegments,
  resolveProcessDurationMs,
  resolveProcessProgress,
  resolveSegmentDurationMs,
  resolveSegmentGroupKey,
  summarizeCapabilities,
} from "../executionProcess.ts";
import type {
  Message,
  MessagePart,
  SubagentPart,
  ToolPart,
} from "../../../../types/message.ts";

function tool(overrides: Partial<ToolPart> & { name: string }): ToolPart {
  return {
    type: "tool",
    args: {},
    ...overrides,
  };
}

function draftResult(): string {
  return JSON.stringify({ entity_type: "scheduled_task_draft", draft_id: "d1" });
}

test("partition routes process parts to the group and keeps original indices", () => {
  const parts: MessagePart[] = [
    { type: "text", content: "hello" },
    { type: "thinking", content: "hmm", thinking_id: "t1" },
    tool({ name: "read_file", args: { file_path: "a.ts" } }),
    { type: "text", content: "middle" },
    {
      type: "subagent",
      agent_id: "a1",
      agent_name: "code",
      input: "do it",
      depth: 1,
      parts: [tool({ name: "read_file", args: { file_path: "inner.ts" } })],
    } as SubagentPart,
    { type: "sandbox", status: "ready", sandbox_id: "sb1" },
    { type: "summary", content: "done" },
  ];
  const { processEntries, bodyEntries } = partitionMessageParts(parts);

  assert.deepEqual(
    processEntries.map((e) => e.part.type),
    ["thinking", "tool", "subagent", "sandbox", "summary"],
  );
  // 原始下标保留（组内渲染必须回传，design §7.2）
  assert.deepEqual(
    processEntries.map((e) => e.partIndex),
    [1, 2, 4, 5, 6],
  );
  assert.deepEqual(
    bodyEntries.map((e) => e.part.type),
    ["text", "text"],
  );
  assert.deepEqual(
    bodyEntries.map((e) => e.partIndex),
    [0, 3],
  );
});

test("partition exiles draft confirm cards, reveal artifacts, todo, cancelled, recommend", () => {
  const parts: MessagePart[] = [
    tool({ name: "draft_scheduled_task", args: {}, success: true, result: draftResult() }),
    tool({ name: "reveal_file", args: { path: "x.png" } }),
    tool({ name: "reveal_project", args: { project_path: "p" } }),
    { type: "todo", items: [] },
    { type: "cancelled" },
    { type: "recommend_questions", questions: [] },
    { type: "token_usage", input_tokens: 1, output_tokens: 1, total_tokens: 2 },
  ];
  const { processEntries, bodyEntries } = partitionMessageParts(parts);
  assert.equal(processEntries.length, 0);
  assert.equal(bodyEntries.length, parts.length);
});

test("partition keeps pending/failed/unparseable drafts in the process set", () => {
  const parts: MessagePart[] = [
    // pending 时 MessagePartRenderer 回落通用 ToolCallItem（:225-234），仍属过程
    tool({ name: "draft_scheduled_task", args: {}, isPending: true }),
    tool({ name: "draft_scheduled_task", args: {}, success: false, result: "boom" }),
    tool({ name: "draft_scheduled_task", args: {}, success: true, result: "not-json" }),
    // MCP 冒号后缀的草稿工具名同样识别
    tool({ name: "server:draft_scheduled_task", args: {}, success: true, result: draftResult() }),
  ];
  const { processEntries, bodyEntries } = partitionMessageParts(parts);
  assert.equal(processEntries.length, 3);
  assert.equal(bodyEntries.length, 1);
  assert.equal(bodyEntries[0]?.partIndex, 3);
});

test("process segments split at body boundaries and keep raw indices", () => {
  const parts: MessagePart[] = [
    tool({ name: "read_file", args: { file_path: "a.ts" } }),
    { type: "thinking", content: "hmm", thinking_id: "t1" },
    { type: "text", content: "middle narrative" },
    tool({ name: "execute", args: { command: "pnpm build" } }),
    { type: "todo", items: [] },
    tool({ name: "grep", args: { pattern: "x" } }),
    tool({ name: "ls", args: {} }),
  ];
  const { processEntries } = partitionMessageParts(parts);
  const segments = partitionProcessSegments(processEntries);
  assert.equal(segments.length, 3);
  assert.deepEqual(
    segments.map((seg) => seg.map((e) => e.partIndex)),
    [[0, 1], [3], [5, 6]],
  );
  // 段键：messageId + 段首原始下标
  assert.equal(makeProcessSegmentKey("m1", segments[0]!), "m1#0");
  assert.equal(makeProcessSegmentKey("m1", segments[1]!), "m1#3");
  assert.equal(makeProcessSegmentKey("m1", segments[2]!), "m1#5");
});

test("text-tools-text-tools interleaving yields two groups, body never merged in", () => {
  const parts: MessagePart[] = [
    { type: "text", content: "intro" },
    tool({ name: "read_file", args: { file_path: "a.ts" } }),
    tool({ name: "grep", args: { pattern: "x" } }),
    { type: "text", content: "mid" },
    tool({ name: "execute", args: { command: "ls" } }),
    tool({ name: "ls", args: {} }),
    { type: "text", content: "outro" },
  ];
  const { processEntries, bodyEntries } = partitionMessageParts(parts);
  const segments = partitionProcessSegments(processEntries);
  assert.equal(segments.length, 2);
  assert.deepEqual(
    segments.map((seg) => seg.map((e) => e.partIndex)),
    [[1, 2], [4, 5]],
  );
  // body（3 个 text）全部在外，永不进段
  assert.deepEqual(
    bodyEntries.map((e) => e.partIndex),
    [0, 3, 6],
  );
});

test("body parts between tool runs prevent merging", () => {
  const parts: MessagePart[] = [
    tool({ name: "execute", args: { command: "a" } }),
    { type: "cancelled" },
    { type: "recommend_questions", questions: [] },
    tool({ name: "execute", args: { command: "b" } }),
  ];
  const { processEntries } = partitionMessageParts(parts);
  const segments = partitionProcessSegments(processEntries);
  assert.equal(segments.length, 2);
  assert.deepEqual(
    segments.map((seg) => seg.map((e) => e.partIndex)),
    [[0], [3]],
  );
});

test("resolveSegmentGroupKey degrades to messageId for single segment", () => {
  const parts: MessagePart[] = [
    tool({ name: "read_file", args: {} }),
    tool({ name: "execute", args: {} }),
  ];
  const { processEntries } = partitionMessageParts(parts);
  const segments = partitionProcessSegments(processEntries);
  assert.equal(segments.length, 1);
  assert.equal(resolveSegmentGroupKey("m9", segments[0]!, segments.length), "m9");
});

test("resolveSegmentGroupKey is segment-aware for multi-segment messages", () => {
  const parts: MessagePart[] = [
    tool({ name: "read_file", args: {} }),
    { type: "text", content: "mid" },
    tool({ name: "execute", args: {} }),
  ];
  const { processEntries } = partitionMessageParts(parts);
  const segments = partitionProcessSegments(processEntries);
  assert.equal(segments.length, 2);
  assert.equal(resolveSegmentGroupKey("m1", segments[0]!, segments.length), "m1#0");
  assert.equal(resolveSegmentGroupKey("m1", segments[1]!, segments.length), "m1#2");
  // 段内追加步骤不改变段键（段首下标稳定）
  const grown = [...segments[1]!, { part: tool({ name: "ls", args: {} }), partIndex: 3 }];
  assert.equal(resolveSegmentGroupKey("m1", grown, segments.length), "m1#2");
});

test("classifyGenericToolKind maps names by content semantics", () => {
  const cases: Array<[string, ReturnType<typeof classifyGenericToolKind>]> = [
    ["read_document", "read"],
    ["kb:fetch_page", "read"],
    ["storage:get_file", "read"],
    ["create_note", "write"],
    ["skill:update_record", "write"],
    ["edit_config", "edit"],
    ["srv:replace_text", "edit"],
    ["web_search", "grep"],
    ["kb:query_docs", "grep"],
    ["grep_code", "grep"],
    ["shell_exec", "execute"],
    ["sys:run_command", "execute"],
    ["deploy_app", "execute"],
    ["list_items", "ls"],
    ["match_pattern", "glob"],
  ];
  for (const [name, expected] of cases) {
    assert.equal(classifyGenericToolKind(name), expected, name);
  }
});

test("classifyGenericToolKind falls back to null when unclassifiable", () => {
  assert.equal(classifyGenericToolKind("do_thing"), null);
  assert.equal(classifyGenericToolKind("ping"), null);
  assert.equal(classifyGenericToolKind(""), null);
});

test("single-segment messages degrade to one group", () => {
  const parts: MessagePart[] = [
    tool({ name: "read_file", args: {} }),
    tool({ name: "execute", args: {} }),
  ];
  const { processEntries } = partitionMessageParts(parts);
  const segments = partitionProcessSegments(processEntries);
  assert.equal(segments.length, 1);
  assert.equal(makeProcessSegmentKey("m9", segments[0]!), "m9#0");
  assert.equal(partitionProcessSegments([]).length, 0);
});

test("getStepStatus mirrors item pill status derivation", () => {
  const at = (part: MessagePart) => getStepStatus({ part, partIndex: 0 });
  assert.equal(at(tool({ name: "ls", args: {}, isPending: true })), "loading");
  assert.equal(at(tool({ name: "ls", args: {}, success: true })), "success");
  assert.equal(at(tool({ name: "ls", args: {}, success: false })), "error");
  assert.equal(at(tool({ name: "ls", args: {}, cancelled: true })), "cancelled");
  assert.equal(at(tool({ name: "custom", args: {} })), "idle");
  assert.equal(at({ type: "thinking", content: "a", isStreaming: true }), "loading");
  assert.equal(at({ type: "thinking", content: "a" }), "success");
  assert.equal(at({ type: "summary", content: "s" }), "success");
  assert.equal(at({ type: "sandbox", status: "starting" }), "loading");
  assert.equal(at({ type: "sandbox", status: "ready" }), "success");
  assert.equal(at({ type: "sandbox", status: "error", error: "x" }), "error");
  assert.equal(at({ type: "sandbox", status: "cancelled" }), "cancelled");
});

test("computeStepDurationMs only for timed parts", () => {
  assert.equal(
    computeStepDurationMs({
      part: tool({
        name: "execute",
        args: {},
        startedAt: "2026-10-16T00:00:00.000Z",
        completedAt: "2026-10-16T00:00:02.400Z",
      }),
      partIndex: 0,
    }),
    2400,
  );
  // thinking 无时间戳
  assert.equal(
    computeStepDurationMs({ part: { type: "thinking", content: "a" }, partIndex: 0 }),
    undefined,
  );
  // 缺 completedAt
  assert.equal(
    computeStepDurationMs({
      part: tool({ name: "ls", args: {}, startedAt: "2026-10-16T00:00:00.000Z" }),
      partIndex: 0,
    }),
    undefined,
  );
});

test("isSpreadsheetStep flags xlsx skill tools", () => {
  assert.equal(
    isSpreadsheetStep({ kind: "tool", toolName: "read_xlsx", argSummary: "a.xlsx" }),
    true,
  );
  assert.equal(
    isSpreadsheetStep({ kind: "mcp", toolName: "do_thing", argSummary: "result.xlsx" }),
    true,
  );
  assert.equal(
    isSpreadsheetStep({ kind: "tool", toolName: "custom_tool", argSummary: "hi" }),
    false,
  );
  assert.equal(isSpreadsheetStep({ kind: "read", filePath: "a.xlsx" }), false);
});

test("step count and progress share the same caliber", () => {
  const parts: MessagePart[] = [
    { type: "thinking", content: "a" },
    tool({ name: "read_file", args: {}, isPending: true }),
    tool({ name: "execute", args: {}, success: true }),
    {
      type: "subagent",
      agent_id: "a1",
      agent_name: "code",
      input: "x",
      depth: 1,
      isPending: true,
    } as SubagentPart,
    { type: "sandbox", status: "starting" },
    { type: "summary", content: "s", isStreaming: true },
  ];
  const { processEntries } = partitionMessageParts(parts);
  assert.equal(countProcessSteps(processEntries), 6);
  const progress = resolveProcessProgress(processEntries);
  // 已结束：thinking（无 isStreaming）、execute 成功；其余 4 未结束
  assert.deepEqual(progress, { completed: 2, total: 6 });
  assert.equal(progress.total, countProcessSteps(processEntries));
});

test("isProcessEntryFinished treats cancelled tools as finished", () => {
  const entry = { part: tool({ name: "ls", args: {}, cancelled: true }), partIndex: 0 };
  assert.equal(isProcessEntryFinished(entry), true);
});

test("capabilities dedupe in order, cap at five, count overflow", () => {
  const parts: MessagePart[] = [
    { type: "thinking", content: "a" },
    tool({ name: "read_file", args: {} }),
    tool({ name: "read_file", args: {} }),
    tool({ name: "grep", args: {} }),
    tool({ name: "execute", args: {} }),
    {
      type: "subagent",
      agent_id: "a1",
      agent_name: "code",
      input: "x",
      depth: 1,
      parts: [tool({ name: "grep", args: {} }), tool({ name: "ls", args: {} })],
    } as SubagentPart,
    { type: "sandbox", status: "ready" },
    { type: "summary", content: "s" },
    tool({ name: "glob", args: {} }),
    tool({ name: "ls", args: {} }),
  ];
  const { processEntries } = partitionMessageParts(parts);
  const summary = summarizeCapabilities(processEntries);
  assert.equal(summary.kinds.length, MAX_CAPABILITY_ICONS);
  // 子代理只占一位且不递归其内部 parts（内部 grep/ls 不冒泡）
  assert.deepEqual(summary.kinds, ["thinking", "read", "grep", "execute", "subagent"]);
  // 去重后共 9 类（thinking/read/grep/execute/subagent/sandbox/summary/glob/ls），上限 5，溢出 4
  assert.equal(summary.overflowCount, 4);
});

test("capability mapping follows the renderer tool-name chain", () => {
  const cases: Array<[string, ReturnType<typeof getEntryCapability>]> = [
    ["read_file", "read"],
    ["write_file", "write"],
    ["edit_file", "edit"],
    ["grep", "grep"],
    ["glob", "glob"],
    ["ls", "ls"],
    ["execute", "execute"],
    ["myserver:search", "mcp"],
    ["custom_tool", "tool"],
  ];
  for (const [name, expected] of cases) {
    assert.equal(
      getEntryCapability({ part: tool({ name, args: {} }), partIndex: 0 }),
      expected,
    );
  }
});

test("countFailedSteps counts failed tools and error sandboxes", () => {
  const parts: MessagePart[] = [
    tool({ name: "execute", args: {}, success: false }),
    tool({ name: "read_file", args: {}, success: true }),
    tool({ name: "ls", args: {}, isPending: true }),
    { type: "sandbox", status: "error", error: "boom" },
    { type: "sandbox", status: "ready" },
    {
      type: "subagent",
      agent_id: "a1",
      agent_name: "code",
      input: "x",
      depth: 1,
      success: false,
    } as SubagentPart,
  ];
  const { processEntries } = partitionMessageParts(parts);
  // 子代理失败不计入顶层 F（design §5.5 只计 tool + sandbox）
  assert.equal(countFailedSteps(processEntries), 2);
});

test("resolveProcessDurationMs prefers message.duration", () => {
  const message: Message = {
    id: "m1",
    role: "assistant",
    content: "",
    timestamp: new Date(),
    duration: 42000,
    parts: [
      tool({
        name: "execute",
        args: {},
        startedAt: "2026-10-16T00:00:00.000Z",
        completedAt: "2026-10-16T00:05:00.000Z",
      }),
    ],
  };
  assert.equal(resolveProcessDurationMs(message), 42000);
});

test("resolveProcessDurationMs falls back to part timestamps", () => {
  const message: Message = {
    id: "m1",
    role: "assistant",
    content: "",
    timestamp: new Date(),
    parts: [
      { type: "thinking", content: "a" },
      tool({
        name: "read_file",
        args: {},
        startedAt: "2026-10-16T00:00:10.000Z",
        completedAt: "2026-10-16T00:00:12.000Z",
      }),
      tool({
        name: "execute",
        args: {},
        startedAt: "2026-10-16T00:00:05.000Z",
        completedAt: "2026-10-16T00:00:20.000Z",
      }),
    ],
  };
  assert.equal(resolveProcessDurationMs(message), 15000);
});

test("resolveProcessDurationMs returns undefined when nothing is available", () => {
  const noParts: Message = { id: "m1", role: "assistant", content: "", timestamp: new Date() };
  assert.equal(resolveProcessDurationMs(noParts), undefined);

  const thinkingOnly: Message = {
    id: "m2",
    role: "assistant",
    content: "",
    timestamp: new Date(),
    parts: [{ type: "thinking", content: "a" }],
  };
  assert.equal(resolveProcessDurationMs(thinkingOnly), undefined);

  const badRange: Message = {
    id: "m3",
    role: "assistant",
    content: "",
    timestamp: new Date(),
    parts: [
      tool({
        name: "execute",
        args: {},
        startedAt: "2026-10-16T00:00:20.000Z",
        completedAt: "2026-10-16T00:00:05.000Z",
      }),
    ],
  };
  assert.equal(resolveProcessDurationMs(badRange), undefined);
});

test("getCurrentActionSummary picks the last unfinished entry", () => {
  const parts: MessagePart[] = [
    { type: "thinking", content: "a" },
    tool({ name: "read_file", args: { file_path: "a.ts" }, success: true }),
    tool({ name: "execute", args: { command: "pnpm build" }, isPending: true }),
    { type: "sandbox", status: "ready" },
  ];
  const { processEntries } = partitionMessageParts(parts);
  const current = getCurrentActionSummary(processEntries);
  assert.ok(current);
  assert.equal(current.entry.partIndex, 2);
  assert.deepEqual(current.summary, { kind: "execute", command: "pnpm build" });
});

test("getCurrentActionSummary returns null when everything finished", () => {
  const parts: MessagePart[] = [
    { type: "thinking", content: "a" },
    tool({ name: "read_file", args: {}, success: true }),
  ];
  const { processEntries } = partitionMessageParts(parts);
  assert.equal(getCurrentActionSummary(processEntries), null);
  assert.equal(getCurrentActionSummary([]), null);
});

test("getStepSummary covers args shapes and generic arg fallback", () => {
  assert.deepEqual(
    getStepSummary({ part: tool({ name: "read_file", args: { file_path: "a/b.ts" } }), partIndex: 0 }),
    { kind: "read", filePath: "a/b.ts" },
  );
  assert.deepEqual(
    getStepSummary({ part: tool({ name: "grep", args: { pattern: "foo", path: "src" } }), partIndex: 0 }),
    { kind: "grep", pattern: "foo", searchPath: "src" },
  );
  assert.deepEqual(
    getStepSummary({ part: tool({ name: "ls", args: {} }), partIndex: 0 }),
    { kind: "ls", dirPath: "/", dirName: "/" },
  );
  assert.deepEqual(
    getStepSummary({
      part: tool({ name: "ls", args: { path: "/a/b/c" } }),
      partIndex: 0,
    }),
    { kind: "ls", dirPath: "/a/b/c", dirName: "c" },
  );
  const longCommand = `run ${"x".repeat(200)}`;
  const executeSummary = getStepSummary({
    part: tool({ name: "execute", args: { command: longCommand } }),
    partIndex: 0,
  });
  assert.equal(executeSummary.kind, "execute");
  if (executeSummary.kind === "execute") {
    // 与 ExecuteItem pill 同口径：slice(0, 77) + "…"（ExecuteItem.tsx:168）
    assert.equal(executeSummary.command, `${longCommand.slice(0, 77)}…`);
  }
  const mcpSummary = getStepSummary({
    part: tool({ name: "srv:do_thing", args: { query: "hello" } }),
    partIndex: 0,
  });
  assert.deepEqual(mcpSummary, { kind: "mcp", toolName: "do_thing", argSummary: "hello" });
  const noArgSummary = getStepSummary({
    part: tool({ name: "custom_tool", args: { count: 3 } }),
    partIndex: 0,
  });
  assert.deepEqual(noArgSummary, {
    kind: "tool",
    toolName: "custom_tool",
    argSummary: undefined,
  });
  assert.deepEqual(
    getStepSummary({
      part: {
        type: "subagent",
        agent_id: "a1",
        agent_name: "code",
        input: "do it",
        depth: 1,
      } as SubagentPart,
      partIndex: 0,
    }),
    { kind: "subagent", agentName: "code", input: "do it" },
  );
});

test("thinking status is gated by message streaming and last position", () => {
  const streamingThinking: MessagePart = {
    type: "thinking",
    content: "a",
    isStreaming: true,
  };
  const thinkingEntry = { part: streamingThinking, partIndex: 0 };
  // 无 ctx：旧口径回退（part.isStreaming）
  assert.equal(getStepStatus(thinkingEntry), "loading");
  assert.equal(isProcessEntryFinished(thinkingEntry), false);
  // 真正 streaming：消息流式中且为最后一条
  assert.equal(
    getStepStatus(thinkingEntry, { isMessageStreaming: true, isLast: true }),
    "loading",
  );
  assert.equal(
    isProcessEntryFinished(thinkingEntry, { isMessageStreaming: true, isLast: true }),
    false,
  );
  // 消息已完成（残留 isStreaming=true）：按已结束处理
  assert.equal(
    getStepStatus(thinkingEntry, { isMessageStreaming: false, isLast: true }),
    "success",
  );
  assert.equal(
    isProcessEntryFinished(thinkingEntry, { isMessageStreaming: false, isLast: true }),
    true,
  );
  // 流式中但不是最后一条：按已结束处理
  assert.equal(
    getStepStatus(thinkingEntry, { isMessageStreaming: true, isLast: false }),
    "success",
  );
  assert.equal(
    isProcessEntryFinished(thinkingEntry, { isMessageStreaming: true, isLast: false }),
    true,
  );
  // summary 同门控
  const summaryEntry = {
    part: { type: "summary", content: "s", isStreaming: true } as MessagePart,
    partIndex: 1,
  };
  assert.equal(
    getStepStatus(summaryEntry, { isMessageStreaming: false, isLast: true }),
    "success",
  );
  assert.equal(
    getStepStatus(summaryEntry, { isMessageStreaming: true, isLast: true }),
    "loading",
  );
  // tool 分支不受门控影响
  const pendingTool = {
    part: tool({ name: "ls", args: {}, isPending: true }),
    partIndex: 2,
  };
  assert.equal(
    getStepStatus(pendingTool, { isMessageStreaming: false, isLast: true }),
    "loading",
  );
  assert.equal(
    isProcessEntryFinished(pendingTool, { isMessageStreaming: false, isLast: true }),
    false,
  );
});

test("resolveSegmentDurationMs scopes duration to the segment", () => {
  const segA = [
    {
      part: tool({
        name: "read_file",
        args: {},
        startedAt: "2026-10-16T00:00:00.000Z",
        completedAt: "2026-10-16T00:00:10.000Z",
      }),
      partIndex: 0,
    },
  ];
  const segB = [
    {
      part: tool({
        name: "execute",
        args: {},
        startedAt: "2026-10-16T00:01:00.000Z",
        completedAt: "2026-10-16T00:01:05.000Z",
      }),
      partIndex: 2,
    },
  ];
  assert.equal(resolveSegmentDurationMs(segA), 10000);
  assert.equal(resolveSegmentDurationMs(segB), 5000);
  // 纯 thinking 段：无时间戳，不显示假值
  assert.equal(
    resolveSegmentDurationMs([{ part: { type: "thinking", content: "a" }, partIndex: 3 }]),
    undefined,
  );
  // 只有 startedAt：不捏造
  assert.equal(
    resolveSegmentDurationMs([
      {
        part: tool({ name: "ls", args: {}, startedAt: "2026-10-16T00:00:00.000Z" }),
        partIndex: 4,
      },
    ]),
    undefined,
  );
  // 倒挂区间：不返回负值
  assert.equal(
    resolveSegmentDurationMs([
      {
        part: tool({
          name: "ls",
          args: {},
          startedAt: "2026-10-16T00:00:20.000Z",
          completedAt: "2026-10-16T00:00:05.000Z",
        }),
        partIndex: 5,
      },
    ]),
    undefined,
  );
});
