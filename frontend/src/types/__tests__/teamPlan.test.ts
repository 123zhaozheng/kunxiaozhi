import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { normalizeTeamPlanEvent, reduceTeamPlan } from "../teamPlan";

describe("TeamAgent plan event contract", () => {
  it("normalizes a proposed plan with attachment manifest and ordered steps", () => {
    const event = normalizeTeamPlanEvent("approval_required", {
      approval_type: "team_plan",
      approval_id: "approval-1",
      plan_id: "plan-1",
      team_run_id: "run-1",
      plan: {
        summary: "Review the uploaded documents",
        status: "awaiting_confirmation",
        attachments: [
          {
            attachment_id: "file-1",
            name: "brief.pdf",
            status: "materialized",
            sandbox_path: "/workspace/attachments/file-1/brief.pdf",
          },
        ],
        steps: [
          {
            step_id: "extract",
            order: 1,
            objective: "Extract facts",
            member_name: "Researcher",
            dependencies: [],
            expected_artifacts: ["facts.json"],
            status: "queued",
          },
        ],
      },
    });

    const plan = reduceTeamPlan(null, event);
    assert.equal(plan?.plan_id, "plan-1");
    assert.equal(plan?.approval_id, "approval-1");
    assert.equal(plan?.team_run_id, "run-1");
    assert.equal(plan?.status, "awaiting_confirmation");
    assert.equal(plan?.attachments[0]?.status, "materialized");
    assert.equal(plan?.steps[0]?.member_name, "Researcher");
  });

  it("merges step and run updates without dropping the reviewed plan", () => {
    let plan = reduceTeamPlan(
      null,
      normalizeTeamPlanEvent("team:plan", {
        plan_id: "plan-2",
        summary: "Two-step run",
        status: "approved",
        steps: [
          { step_id: "a", order: 1, objective: "A" },
          { step_id: "b", order: 2, objective: "B", dependencies: ["a"] },
        ],
      }),
    );
    plan = reduceTeamPlan(
      plan,
      normalizeTeamPlanEvent("team:step", {
        plan_id: "plan-2",
        step_id: "a",
        status: "succeeded",
        output: "facts.json",
      }),
    );
    plan = reduceTeamPlan(
      plan,
      normalizeTeamPlanEvent("team:run", {
        plan_id: "plan-2",
        status: "partial_failure",
      }),
    );

    assert.equal(plan?.status, "partial_failure");
    assert.equal(plan?.summary, "Two-step run");
    assert.equal(plan?.steps[0]?.status, "succeeded");
    assert.equal(plan?.steps[0]?.output, "facts.json");
    assert.deepEqual(plan?.steps[1]?.dependencies, ["a"]);
  });

  it("maps the backend attachment manifest and approval id payload", () => {
    const plan = reduceTeamPlan(
      null,
      normalizeTeamPlanEvent("approval_required", {
        id: "approval-2",
        plan_id: "plan-3",
        status: "awaiting_confirmation",
        plan: {
          summary: "Use uploaded data",
          attachment_manifest: {
            attachments: [
              {
                id: "file-2",
                name: "data.csv",
                status: "materialized",
                sandbox_path: "/work/attachments/file-2/data.csv",
              },
            ],
          },
          steps: [
            {
              step_id: "inspect",
              ordinal: 0,
              required_artifacts: ["report.json"],
            },
          ],
        },
      }),
    );

    assert.equal(plan?.approval_id, "approval-2");
    assert.equal(plan?.attachments[0]?.attachment_id, "file-2");
    assert.equal(plan?.steps[0]?.order, 1);
    assert.deepEqual(plan?.steps[0]?.expected_artifacts, ["report.json"]);
  });
});
