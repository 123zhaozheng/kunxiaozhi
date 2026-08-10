import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import "../useAgent/eventHandlers.ts";
import {
  useSopStatus,
  buildSopFlowElements,
  buildSopStructureKey,
} from "../useSopStatus.ts";
import { handleStreamEvent, type EventHandlerContext } from "../useAgent/eventHandlers.ts";
import { reconstructMessagesFromEvents } from "../useAgent/historyLoader.ts";
import type { StreamEvent } from "../useAgent/types.ts";
import type { SopPlan, SopStep } from "../../types/sop.ts";
import { SOP_NODE_WIDTH } from "../../components/sop/sopLayout.ts";

// ---- fixtures ----

const baseSteps: SopStep[] = [
  {
    step_id: "s1",
    title: "Research",
    description: "Gather requirements",
    dependencies: [],
    assignee: "team-m-1-researcher",
    expected_output: "requirement list",
    status: "pending",
  },
  {
    step_id: "s2",
    title: "Design",
    description: "Create mockups",
    dependencies: ["s1"],
    assignee: "team-m-2-designer",
    expected_output: "mockups",
    status: "pending",
  },
  {
    step_id: "s3",
    title: "Ship",
    description: "Deploy",
    dependencies: ["s2"],
    assignee: "team-m-3-ops",
    expected_output: "live",
    status: "pending",
  },
];

function makePlan(overrides: Partial<SopPlan> = {}): SopPlan {
  return {
    plan_id: "plan-1",
    session_id: "session-1",
    team_id: "team-1",
    goal: "Build the thing",
    summary: "",
    steps: baseSteps,
    status: "running",
    user_feedback: null,
    ...overrides,
  };
}

function withStatus(plan: SopPlan, stepId: string, status: SopStep["status"]): SopPlan {
  return {
    ...plan,
    steps: plan.steps.map((step) =>
      step.step_id === stepId ? { ...step, status } : step,
    ),
  };
}

// ---- builder / structural key ----

test("buildSopFlowElements derives nodes and dependency edges", () => {
  const plan = makePlan();
  const { nodes, edges } = buildSopFlowElements(plan);

  assert.equal(nodes.length, 3);
  assert.equal(edges.length, 2);
  assert.equal(edges[0].source, "s1");
  assert.equal(edges[0].target, "s2");
  assert.equal(edges[1].source, "s2");
  assert.equal(edges[1].target, "s3");

  const byId = new Map(nodes.map((n) => [n.id, n]));
  assert.equal(byId.get("s1")?.data.status, "pending");
  assert.equal(byId.get("s1")?.data.title, "Research");
  assert.equal(byId.get("s2")?.data.assignee, "team-m-2-designer");
  assert.equal(byId.get("s1")?.style?.width, SOP_NODE_WIDTH);
  assert.equal(
    byId.get("s1")?.style?.height,
    byId.get("s1")?.data.height,
  );
});

test("buildSopFlowElements drops dependencies to unknown steps", () => {
  const { edges } = buildSopFlowElements(
    makePlan({
      steps: [
        { ...baseSteps[0], dependencies: ["missing"] },
        ...baseSteps.slice(1),
      ],
    }),
  );
  assert.equal(edges.some((edge) => edge.source === "missing"), false);
});

test("status-only changes keep coordinates identical (no jitter)", () => {
  const planA = makePlan();
  const planB = withStatus(planA, "s2", "running");
  const planC = withStatus(planB, "s3", "succeeded");

  // Same structure → same structural key (status excluded).
  assert.equal(buildSopStructureKey(planA), buildSopStructureKey(planB));
  assert.equal(buildSopStructureKey(planB), buildSopStructureKey(planC));

  const layoutA = buildSopFlowElements(planA);
  const layoutB = buildSopFlowElements(planB);
  const layoutC = buildSopFlowElements(planC);

  for (const id of ["s1", "s2", "s3"]) {
    const a = layoutA.nodes.find((n) => n.id === id)!;
    const b = layoutB.nodes.find((n) => n.id === id)!;
    const c = layoutC.nodes.find((n) => n.id === id)!;
    assert.deepEqual(b.position, a.position, `${id} position stable`);
    assert.deepEqual(c.position, a.position, `${id} position stable`);
  }
});

// ---- hook ----

function FlowHost({ plan }: { plan: SopPlan | null }) {
  const { nodes, edges } = useSopStatus(plan);
  return (
    <div>
      {nodes.map((node) => (
        <span
          key={node.id}
          data-node-id={node.id}
          data-status={node.data.status}
          data-x={node.position.x}
          data-y={node.position.y}
        />
      ))}
      {edges.map((edge) => (
        <span
          key={edge.id}
          data-edge-id={edge.id}
          data-source={edge.source}
          data-target={edge.target}
        />
      ))}
    </div>
  );
}

function extractPositions(html: string): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  const regex = /data-node-id="([^"]+)" data-status="[^"]*" data-x="([^"]+)" data-y="([^"]+)"/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(html)) !== null) {
    positions[match[1]] = { x: Number(match[2]), y: Number(match[3]) };
  }
  return positions;
}

test("useSopStatus derives nodes/edges from a normalized snapshot", () => {
  const html = renderToStaticMarkup(<FlowHost plan={makePlan()} />);
  const positions = extractPositions(html);
  assert.deepEqual(Object.keys(positions).sort(), ["s1", "s2", "s3"]);
  assert.match(html, /data-source="s1" data-target="s2"/);
  assert.match(html, /data-node-id="s2" data-status="pending"/);
  assert.match(html, /data-node-id="s1" data-status="pending"/);
});

test("useSopStatus updates node status without moving coordinates", () => {
  const htmlA = renderToStaticMarkup(<FlowHost plan={makePlan()} />);
  const htmlB = renderToStaticMarkup(
    <FlowHost plan={withStatus(makePlan(), "s2", "running")} />,
  );
  const htmlC = renderToStaticMarkup(
    <FlowHost plan={withStatus(makePlan(), "s3", "failed")} />,
  );

  const posA = extractPositions(htmlA);
  const posB = extractPositions(htmlB);
  const posC = extractPositions(htmlC);

  for (const id of ["s1", "s2", "s3"]) {
    assert.deepEqual(posB[id], posA[id], `${id} unchanged`);
    assert.deepEqual(posC[id], posA[id], `${id} unchanged`);
  }
  assert.match(htmlB, /data-node-id="s2" data-status="running"/);
  assert.match(htmlC, /data-node-id="s3" data-status="failed"/);
});

test("useSopStatus returns no nodes for null plan", () => {
  const html = renderToStaticMarkup(<FlowHost plan={null} />);
  assert.doesNotMatch(html, /data-node-id/);
});

// ---- eventHandlers branches ----

interface MockCtx extends EventHandlerContext {
  sopPlanUpdaters: Array<(prev: SopPlan | null) => SopPlan | null>;
  approvals: unknown[];
}

function createMockCtx(overrides: Partial<MockCtx> = {}): MockCtx {
  const approvals: unknown[] = [];
  const sopPlanUpdaters: Array<(prev: SopPlan | null) => SopPlan | null> = [];
  const base: MockCtx = {
    options: {
      onApprovalRequired: (approval) => {
        approvals.push(approval);
      },
    },
    sessionIdRef: { current: null },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => {},
    setMessages: () => {},
    setConnectionStatus: () => {},
    setIsInitializingSandbox: () => {},
    setSandboxError: () => {},
    setActiveGoal: () => {},
    setGoalsByRunId: () => {},
    sopPlanUpdaters,
    approvals,
    setSopPlan: (updater) => {
      sopPlanUpdaters.push(
        updater as (prev: SopPlan | null) => SopPlan | null,
      );
    },
  };
  return { ...base, ...overrides };
}

function makeStreamEvent(eventType: StreamEvent["event"], data: unknown): StreamEvent {
  return { event: eventType, data: JSON.stringify(data) };
}

const snapshot = {
  plan_id: "plan-1",
  session_id: "session-1",
  team_id: "team-1",
  goal: "Build the thing",
  steps: baseSteps.map((step) => ({ ...step })),
  status: "awaiting_confirmation",
  user_feedback: null,
};

test("sop:updated event updates the SOP plan state", () => {
  const ctx = createMockCtx();
  handleStreamEvent(
    makeStreamEvent("sop:updated", snapshot),
    "msg-1",
    "evt-1",
    undefined,
    ctx,
  );

  assert.equal(ctx.sopPlanUpdaters.length, 1);
  const plan = ctx.sopPlanUpdaters[0](null);
  assert.equal(plan?.plan_id, "plan-1");
  assert.equal(plan?.status, "awaiting_confirmation");
  assert.equal(plan?.steps.length, 3);
});

test("stale connection events cannot replace the active SOP plan", () => {
  const ctx = createMockCtx();
  ctx.streamVersionRef.current = 2;
  handleStreamEvent(
    makeStreamEvent("sop:updated", snapshot),
    "msg-1",
    "stale-evt",
    undefined,
    ctx,
    1,
  );
  assert.equal(ctx.sopPlanUpdaters.length, 0);
});

test("approval_required(sop_plan) sets the plan with approval_id and skips the generic approval panel", () => {
  const ctx = createMockCtx();
  handleStreamEvent(
    makeStreamEvent("approval_required", {
      id: "approval-9",
      message: "Confirm this SOP?",
      type: "sop_plan",
      plan_id: "plan-1",
      plan: snapshot,
    }),
    "msg-1",
    "evt-2",
    undefined,
    ctx,
  );

  assert.equal(ctx.approvals.length, 0, "must not fall into generic ApprovalPanel");
  assert.equal(ctx.sopPlanUpdaters.length, 1);
  const plan = ctx.sopPlanUpdaters[0](null);
  assert.equal(plan?.approval_id, "approval-9");
  assert.equal(plan?.plan_id, "plan-1");
});

test("approval_required(sop_plan) keeps approval_id across a later sop:updated snapshot", () => {
  const ctx = createMockCtx();
  handleStreamEvent(
    makeStreamEvent("approval_required", {
      id: "approval-9",
      type: "sop_plan",
      plan: snapshot,
    }),
    "msg-1",
    "evt-2",
    undefined,
    ctx,
  );
  handleStreamEvent(
    makeStreamEvent("sop:updated", { ...snapshot, status: "running" }),
    "msg-1",
    "evt-3",
    undefined,
    ctx,
  );

  let current: SopPlan | null = null;
  for (const updater of ctx.sopPlanUpdaters) {
    current = updater(current);
  }
  assert.equal(current?.approval_id, "approval-9");
  assert.equal(current?.status, "running");
});

test("non-sop approval_required does not touch the SOP plan", () => {
  const ctx = createMockCtx();
  handleStreamEvent(
    makeStreamEvent("approval_required", {
      id: "approval-1",
      message: "Fill this form",
      type: "form",
      fields: [],
    }),
    "msg-1",
    "evt-4",
    undefined,
    ctx,
  );
  assert.equal(ctx.sopPlanUpdaters.length, 0);
});

// ---- historyLoader: sop events never enter message bodies ----

interface HistoryLike {
  event_type: string;
  data: unknown;
  run_id?: string;
}

const snapshotForHistory = {
  plan_id: "plan-1",
  session_id: "session-1",
  team_id: "team-1",
  goal: "Build the thing",
  steps: baseSteps.map((step) => ({ ...step })),
  status: "awaiting_confirmation",
  user_feedback: null,
};

test("history replay does not put sop events into message bodies", () => {
  const approvals: unknown[] = [];
  const events: HistoryLike[] = [
    {
      event_type: "user:message",
      data: { content: "hi", message_id: "u1" },
      run_id: "run-1",
    },
    { event_type: "sop:updated", data: snapshotForHistory, run_id: "run-1" },
    {
      event_type: "approval_required",
      data: { id: "a1", type: "sop_plan", plan: snapshotForHistory },
      run_id: "run-1",
    },
    { event_type: "done", data: {}, run_id: "run-1" },
  ];

  const messages = reconstructMessagesFromEvents(
    events as never,
    new Set<string>(),
    {
      options: { onApprovalRequired: (a) => approvals.push(a) },
      activeSubagentStack: [],
    },
  );

  // Only the user message survives — sop events must not spawn assistant
  // bubbles, add sop parts, or rehydrate the generic approval list.
  assert.equal(messages.length, 1);
  assert.equal(messages[0].role, "user");
  assert.ok(
    !messages.some((m) =>
      (m.parts ?? []).some((p) => (p as { type: string }).type === "sop"),
    ),
  );
  assert.equal(approvals.length, 0);
});

test("history replay attaches sop events to an existing assistant bubble without adding a sop part", () => {
  const approvals: unknown[] = [];
  const events: HistoryLike[] = [
    {
      event_type: "user:message",
      data: { content: "hi", message_id: "u1" },
      run_id: "run-1",
    },
    {
      event_type: "message:chunk",
      data: { content: "Let me plan this." },
      run_id: "run-1",
    },
    { event_type: "sop:updated", data: snapshotForHistory, run_id: "run-1" },
    {
      event_type: "approval_required",
      data: { id: "a1", type: "sop_plan", plan: snapshotForHistory },
      run_id: "run-1",
    },
  ];

  const messages = reconstructMessagesFromEvents(
    events as never,
    new Set<string>(),
    {
      options: { onApprovalRequired: (a) => approvals.push(a) },
      activeSubagentStack: [],
    },
  );

  assert.equal(messages.length, 2);
  const assistant = messages.find((m) => m.role === "assistant");
  assert.ok(assistant, "assistant bubble exists");
  assert.match(assistant?.content ?? "", /Let me plan this/);
  assert.ok(
    !(assistant?.parts ?? []).some(
      (p) => (p as { type: string }).type === "sop",
    ),
    "no sop part in the assistant message",
  );
  assert.equal(approvals.length, 0);
});
