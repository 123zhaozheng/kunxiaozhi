import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import "../../../i18n";
import { SopBlock } from "../SopBlock.tsx";
import { buildSopRespondResponse } from "../sopBlockUtils.ts";
import type { SopPlan } from "../../../types/sop.ts";

function makePlan(overrides: Partial<SopPlan> = {}): SopPlan {
  return {
    plan_id: "plan-1",
    session_id: "session-1",
    team_id: "team-1",
    goal: "Ship the feature",
    summary: "",
    steps: [
      {
        step_id: "s1",
        title: "Research",
        dependencies: [],
        assignee: "team-m-1-researcher",
        expected_output: "requirement list",
        status: "succeeded",
      },
      {
        step_id: "s2",
        title: "Design",
        dependencies: ["s1"],
        assignee: "team-m-2-designer",
        expected_output: "mockups",
        status: "pending",
      },
    ],
    status: "awaiting_confirmation",
    user_feedback: null,
    approval_id: "approval-1",
    ...overrides,
  };
}

test("returns null when plan is null", () => {
  const html = renderToStaticMarkup(<SopBlock plan={null} />);
  assert.equal(html, "");
});

test("renders card title, goal, progress and legend", () => {
  const html = renderToStaticMarkup(
    <SopBlock plan={makePlan()} onRespond={() => {}} />,
  );
  assert.match(html, /SOP plan/);
  assert.match(html, /Ship the feature/);
  // 1 of 2 steps succeeded → progress text and percent
  assert.match(html, /1\/2/);
  assert.match(html, /50%/);
  // legend entries
  assert.match(html, /Pending/);
  assert.match(html, /Running/);
  assert.match(html, /Done/);
  assert.match(html, /Failed/);
  assert.match(html, /Cancelled/);
});

test("shows the awaiting_confirmation status badge", () => {
  const html = renderToStaticMarkup(<SopBlock plan={makePlan()} />);
  assert.match(html, /Awaiting confirmation/);
});

test("shows running/completed/rejected status badges", () => {
  const running = renderToStaticMarkup(
    <SopBlock plan={makePlan({ status: "running" })} />,
  );
  assert.match(running, /Running/);

  const completed = renderToStaticMarkup(
    <SopBlock plan={makePlan({ status: "completed" })} />,
  );
  assert.match(completed, /Completed/);

  const rejected = renderToStaticMarkup(
    <SopBlock
      plan={makePlan({ status: "rejected", user_feedback: "too broad" })}
    />,
  );
  assert.match(rejected, /Rejected/);
  assert.match(rejected, /too broad/);
});

test("renders confirm and replan buttons only when approval is pending", () => {
  const awaiting = makePlan({ status: "awaiting_confirmation" });
  const html = renderToStaticMarkup(
    <SopBlock plan={awaiting} onRespond={() => {}} />,
  );
  assert.match(html, /Confirm and run/);
  assert.match(html, /Replan/);

  // Running plan → no approval buttons.
  const running = renderToStaticMarkup(
    <SopBlock plan={makePlan({ status: "running" })} onRespond={() => {}} />,
  );
  assert.doesNotMatch(running, /Confirm and run/);
  assert.doesNotMatch(running, /Replan/);

  // No onRespond → no buttons even when awaiting confirmation.
  const noHandler = renderToStaticMarkup(<SopBlock plan={awaiting} />);
  assert.doesNotMatch(noHandler, /Confirm and run/);
  assert.doesNotMatch(noHandler, /Replan/);
});

test("buildSopRespondResponse builds approval payloads", () => {
  assert.deepEqual(buildSopRespondResponse(true, "ignore me"), {});
  assert.deepEqual(buildSopRespondResponse(false, "  make it smaller  "), {
    feedback: "make it smaller",
  });
  assert.deepEqual(buildSopRespondResponse(false, "   "), {});
});
