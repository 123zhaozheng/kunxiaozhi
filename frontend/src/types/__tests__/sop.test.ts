import assert from "node:assert/strict";
import test from "node:test";
import {
  normalizeSopEvent,
  reduceSop,
  isSopReplayEvent,
  type SopPlan,
} from "../sop.ts";

// ---- fixtures ----

const snapshot = {
  plan_id: "plan-1",
  session_id: "session-1",
  team_id: "team-1",
  goal: "Build a landing page",
  summary: "Three step plan",
  steps: [
    {
      step_id: "s1",
      title: "Research",
      description: "Gather requirements",
      dependencies: [],
      assignee: "team-m-1-researcher",
      stage: "stage-1",
      expected_output: "requirement list",
      acceptance_criteria: ["covers all inputs"],
      status: "succeeded",
      attempts: 1,
      output: "done",
      error: null,
    },
    {
      step_id: "s2",
      title: "Design",
      description: "Create mockups",
      dependencies: ["s1"],
      assignee: "team-m-2-designer",
      expected_output: "mockups",
      status: "pending",
      attempts: 0,
      output: null,
      error: null,
    },
  ],
  status: "running",
  user_feedback: null,
  created_at: "2026-08-04T00:00:00Z",
  updated_at: "2026-08-04T00:00:01Z",
};

// ---- normalizeSopEvent ----

test("normalizeSopEvent parses a full sop:updated snapshot", () => {
  const plan = normalizeSopEvent(snapshot);
  assert.ok(plan);
  assert.equal(plan.plan_id, "plan-1");
  assert.equal(plan.session_id, "session-1");
  assert.equal(plan.team_id, "team-1");
  assert.equal(plan.goal, "Build a landing page");
  assert.equal(plan.status, "running");
  assert.equal(plan.steps.length, 2);
  assert.equal(plan.steps[0].status, "succeeded");
  assert.equal(plan.steps[1].dependencies[0], "s1");
  assert.equal(plan.approval_id, undefined);
});

test("normalizeSopEvent tolerates backend field aliases", () => {
  const plan = normalizeSopEvent({
    id: "plan-aliased",
    steps: [
      {
        step_id: "s1",
        title: "Step one",
        dependencies: [],
        assignee: "team-m-1-researcher",
        expectedOutput: "alias output",
        status: "pending",
      },
    ],
    status: "awaiting_confirmation",
  });
  assert.ok(plan);
  assert.equal(plan.plan_id, "plan-aliased");
  assert.equal(plan.steps[0].expected_output, "alias output");
  assert.equal(plan.status, "awaiting_confirmation");
});

test("normalizeSopEvent normalizes unknown statuses to safe defaults", () => {
  const plan = normalizeSopEvent({
    plan_id: "plan-x",
    steps: [
      {
        step_id: "s1",
        title: "Step",
        dependencies: [],
        assignee: "team-m-1-researcher",
        expected_output: "out",
        status: "exploded",
      },
    ],
    status: "obliterated",
  });
  assert.ok(plan);
  assert.equal(plan.status, "draft");
  assert.equal(plan.steps[0].status, "pending");
});

test("normalizeSopEvent preserves expired approval state", () => {
  const plan = normalizeSopEvent({
    ...snapshot,
    status: "timed_out",
    approval_id: "approval-expired",
  });
  assert.equal(plan?.status, "timed_out");
  assert.equal(plan?.approval_id, "approval-expired");
});

test("normalizeSopEvent extracts approval_id from approval_required payload", () => {
  const plan = normalizeSopEvent({
    id: "approval-42",
    message: "Confirm this SOP?",
    type: "sop_plan",
    plan_id: snapshot.plan_id,
    plan: snapshot,
  });
  assert.ok(plan);
  assert.equal(plan.approval_id, "approval-42");
  assert.equal(plan.plan_id, "plan-1");
  assert.equal(plan.steps.length, 2);
  assert.equal(plan.status, "running");
});

test("normalizeSopEvent returns null for invalid payloads", () => {
  assert.equal(normalizeSopEvent(null), null);
  assert.equal(normalizeSopEvent("nope"), null);
  assert.equal(normalizeSopEvent({ plan_id: "no-steps" }), null);
  assert.equal(normalizeSopEvent({ steps: [] }), null);
});

// ---- reduceSop ----

test("reduceSop fully replaces the plan snapshot", () => {
  const first = normalizeSopEvent(snapshot);
  const second = reduceSop(first, {
    event_type: "sop:updated",
    data: {
      ...snapshot,
      status: "completed",
      steps: snapshot.steps.map((step, i) =>
        i === 0 ? step : { ...step, status: "succeeded" },
      ),
    },
  });
  assert.ok(second);
  assert.equal(second.status, "completed");
  assert.equal(second.steps[1].status, "succeeded");
  assert.equal(second.steps[1].dependencies[0], "s1");
});

test("reduceSop preserves approval_id when the snapshot lacks it", () => {
  const current = normalizeSopEvent({
    id: "approval-7",
    type: "sop_plan",
    plan: snapshot,
  });
  assert.equal(current?.approval_id, "approval-7");

  const next = reduceSop(current, {
    event_type: "sop:updated",
    data: { ...snapshot, status: "running" },
  });
  assert.equal(next?.approval_id, "approval-7");
  assert.equal(next?.status, "running");
});

test("reduceSop returns current on invalid events", () => {
  const current = normalizeSopEvent(snapshot);
  assert.equal(reduceSop(current, null), current);
  assert.equal(reduceSop(current, "junk"), current);
  assert.equal(reduceSop(current, { plan_id: "x" }), current);
});

test("reduceSop clears approval_id when a new plan replaces the current plan", () => {
  const current = normalizeSopEvent({ id: "approval-old", type: "sop_plan", plan: snapshot });
  const next = reduceSop(current, {
    event_type: "sop:updated",
    data: { ...snapshot, plan_id: "plan-new", status: "awaiting_confirmation" },
  });
  assert.equal(next?.plan_id, "plan-new");
  assert.equal(next?.approval_id, undefined);
});

// ---- isSopReplayEvent ----

test("isSopReplayEvent detects sop history events", () => {
  assert.equal(
    isSopReplayEvent({ event_type: "sop:updated", data: snapshot }),
    true,
  );
  assert.equal(
    isSopReplayEvent({
      event_type: "approval_required",
      data: { id: "a1", type: "sop_plan", plan: snapshot },
    }),
    true,
  );
  assert.equal(
    isSopReplayEvent({
      event_type: "approval_required",
      data: { id: "a1", type: "form", fields: [] },
    }),
    false,
  );
  assert.equal(isSopReplayEvent({ event_type: "message:chunk", data: {} }), false);
  assert.equal(
    isSopReplayEvent({ event_type: "approval_required", data: { plan: snapshot } }),
    false,
  );
  assert.equal(isSopReplayEvent("junk"), false);
});

// ---- snapshot shape guard: unknown plan fields never leak in ----

test("normalized plan only carries known fields", () => {
  const plan = normalizeSopEvent(snapshot) as SopPlan;
  assert.deepEqual(Object.keys(plan).sort(), [
    "approval_expires_at",
    "approval_id",
    "created_at",
    "goal",
    "plan_id",
    "session_id",
    "status",
    "steps",
    "summary",
    "team_id",
    "updated_at",
    "user_feedback",
  ]);
});
