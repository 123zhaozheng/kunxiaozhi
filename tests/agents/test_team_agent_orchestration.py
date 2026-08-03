from types import SimpleNamespace

import pytest

from src.agents.team_agent.attachments import AttachmentManifest
from src.agents.team_agent.orchestration import (
    TeamHandoff,
    TeamRoleResult,
    TeamRunStatus,
    TeamTaskGuardMiddleware,
    build_team_plan,
    resolve_team_run_status,
    validate_handoff_for_plan,
)
from src.agents.team_agent.roster import compile_team_roster


def _plan():
    team = SimpleNamespace(
        members=[SimpleNamespace(member_id="m1", role_name="Research", position=0, enabled=True)],
        default_member_id="m1",
    )
    roster = compile_team_roster(team)
    return build_team_plan(
        user_input="inspect the attachment",
        team_run_id="run-1",
        roster=roster,
        attachment_manifest=AttachmentManifest(work_dir="/work"),
    )


def test_plan_is_approval_gated_and_handoff_is_json_round_trip():
    plan = _plan()
    assert plan.requires_approval is True
    handoff = TeamHandoff(
        plan_id=plan.plan_id,
        team_run_id=plan.team_run_id,
        step_id="step-1",
        subagent_type=plan.steps[0].subagent_type,
        objective="inspect",
    )
    with pytest.raises(PermissionError):
        validate_handoff_for_plan(handoff, plan)
    assert TeamHandoff.from_task_description(handoff.to_task_description()).step_id == "step-1"


def test_required_step_failure_cannot_report_success():
    plan = _plan().approved()
    result = TeamRoleResult(
        handoff_id="handoff-1",
        plan_id=plan.plan_id,
        team_run_id=plan.team_run_id,
        step_id="step-1",
        subagent_type=plan.steps[0].subagent_type,
        success=False,
        error="provider failed",
    )
    assert resolve_team_run_status(plan, {"step-1": result}) is TeamRunStatus.PARTIAL_FAILURE
    assert resolve_team_run_status(plan, {}, cancelled=True) is TeamRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_task_guard_rejects_incomplete_dependencies():
    plan = _plan().model_copy(
        update={
            "steps": (
                _plan().steps[0].model_copy(update={"step_id": "first"}),
                _plan().steps[0].model_copy(
                    update={
                        "step_id": "second",
                        "dependencies": ("first",),
                    }
                ),
            )
        }
    ).approved()
    guard = TeamTaskGuardMiddleware(plan)
    handoff = TeamHandoff(
        plan_id=plan.plan_id,
        team_run_id=plan.team_run_id,
        step_id="second",
        subagent_type=plan.steps[1].subagent_type or "",
        objective="second",
    )

    with pytest.raises(PermissionError, match="dependencies"):
        await guard.awrap_tool_call(
            SimpleNamespace(tool_call={"name": "task", "args": {"description": handoff.to_task_description(), "subagent_type": handoff.subagent_type}}),
            lambda _request: None,
        )
