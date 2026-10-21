import assert from "node:assert/strict";
import test from "node:test";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ExecutionProcessGroup } from "../ExecutionProcessGroup.tsx";
import { clearProcessGroupOverrides, setProcessGroupExpanded } from "../executionProcessPanelState.ts";
import { buildStepPanelPayload, refreshOpenStepPanel } from "../processStepDetails.tsx";
import {
  closePersistentToolPanel,
  getPersistentToolPanelState,
  openPersistentToolPanel,
} from "../items/persistentToolPanelState.tsx";
import type {
  SubagentPart,
  SummaryPart,
  ThinkingPart,
  ToolPart,
} from "../../../../types/message";

const t = (key: string) => key;

const thinking = (content: string, isStreaming = true): ThinkingPart => ({
  type: "thinking",
  content,
  isStreaming,
  thinking_id: "think-1",
});

const summary = (content: string, isStreaming = true): SummaryPart => ({
  type: "summary",
  content,
  isStreaming,
  summary_id: "s1",
});

function panelContent(): unknown {
  const children = getPersistentToolPanelState()?.children as ReactElement<{
    children: ReactElement<{ content: string; isStreaming: boolean }>;
  }>;
  return children.props.children.props;
}

test("thinking/summary panel status follows the gated streaming flag", () => {
  // Residual isStreaming=true on a finished message must not spin forever.
  for (const part of [thinking("x"), summary("x")]) {
    assert.equal(buildStepPanelPayload({ part, isStreaming: false }, { Icon16: null, t })?.status, "success");
    assert.equal(buildStepPanelPayload({ part, isStreaming: true }, { Icon16: null, t })?.status, "loading");
    assert.equal(buildStepPanelPayload({ part }, { Icon16: null, t })?.status, "loading");
  }
});

test("an open thinking panel refreshes with streamed content and final status", () => {
  closePersistentToolPanel();
  const opened = buildStepPanelPayload({ part: thinking("a"), isStreaming: true }, { Icon16: null, t })!;
  openPersistentToolPanel({ ...opened, children: opened.children });

  assert.equal(refreshOpenStepPanel(thinking("ab"), true, t), true);
  assert.deepEqual(panelContent(), { content: "ab", isStreaming: true });
  assert.equal(getPersistentToolPanelState()?.status, "loading");

  assert.equal(refreshOpenStepPanel(thinking("abc"), false, t), true);
  assert.deepEqual(panelContent(), { content: "abc", isStreaming: false });
  assert.equal(getPersistentToolPanelState()?.status, "success");
  closePersistentToolPanel();
});

test("an open summary panel refreshes too", () => {
  closePersistentToolPanel();
  const opened = buildStepPanelPayload({ part: summary("a"), isStreaming: true }, { Icon16: null, t })!;
  openPersistentToolPanel({ ...opened });

  assert.equal(refreshOpenStepPanel(summary("ab"), false, t), true);
  assert.equal(getPersistentToolPanelState()?.status, "success");
  closePersistentToolPanel();
});

test("refresh never rewrites another open panel", () => {
  closePersistentToolPanel();
  openPersistentToolPanel({ title: "Tool", status: "success", children: "tool body", panelKey: "tool:1" });

  assert.equal(refreshOpenStepPanel(thinking("x"), true, t), false);
  assert.equal(refreshOpenStepPanel({ ...thinking("x"), thinking_id: undefined }, true, t), false);
  assert.equal(getPersistentToolPanelState()?.children, "tool body");
  closePersistentToolPanel();
});

const tool: ToolPart = {
  type: "tool",
  name: "grep",
  args: { pattern: "needle" },
  result: "a.ts:1:needle",
  success: true,
};

const subagent: SubagentPart = {
  type: "subagent",
  agent_id: "agent-1",
  agent_name: "research_agent",
  input: "look things up",
  success: true,
  depth: 1,
  status: "complete",
};

function renderGroup(entries: Array<ToolPart | SubagentPart>): string {
  return renderToStaticMarkup(
    createElement(ExecutionProcessGroup, {
      messageId: "m1",
      processEntries: entries.map((part, partIndex) => ({ part, partIndex })),
      isStreaming: false,
      messagePartCount: entries.length + 1,
    }),
  );
}

test("a collapsed group keeps subagent cards mounted but hidden", () => {
  clearProcessGroupOverrides();
  const collapsed = renderGroup([tool, subagent]);
  assert.match(collapsed, /pl-3 py-0\.5 hidden/);
  assert.match(collapsed, /Research Agent/);
  assert.doesNotMatch(collapsed, /aria-label="[^"]*needle/);

  setProcessGroupExpanded("m1", true);
  const expanded = renderGroup([tool, subagent]);
  assert.doesNotMatch(expanded, /pl-3 py-0\.5 hidden/);
  assert.match(expanded, /aria-label="[^"]*needle/);
  clearProcessGroupOverrides();
});

test("a collapsed group without subagents renders no step list", () => {
  clearProcessGroupOverrides();
  assert.doesNotMatch(renderGroup([tool]), /border-l/);
});
