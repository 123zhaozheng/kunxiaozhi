"""SOP 数据模型与 validate_sop_plan 确定性校验测试。"""

from __future__ import annotations

from src.agents.team_agent.sop.schemas import SOPPlan, SOPStep, validate_sop_plan

_ROSTER = ["team-m1-role1", "team-m2-role2", "team-m3-role3"]
_MAX_STEPS = 8
_MIN_STEPS = 2


def _step(step_id: str, **overrides) -> SOPStep:
    fields = {
        "step_id": step_id,
        "title": f"step {step_id}",
        "assignee": "team-m1-role1",
        "expected_output": f"output of {step_id}",
    }
    fields.update(overrides)
    return SOPStep(**fields)


def _plan(steps: list[SOPStep], **overrides) -> SOPPlan:
    return SOPPlan(
        plan_id="plan-1",
        session_id="session-1",
        team_id="team-1",
        goal="build a report",
        steps=steps,
        **overrides,
    )


def _valid_plan() -> SOPPlan:
    return _plan(
        [
            _step("s1"),
            _step("s2", dependencies=["s1"], assignee="team-m2-role2"),
        ]
    )


def test_valid_plan_passes() -> None:
    errors = validate_sop_plan(_valid_plan(), _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert errors == []


def test_empty_steps_errors() -> None:
    errors = validate_sop_plan(_plan([]), _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert any("不能为空" in error for error in errors)


def test_too_many_steps_errors() -> None:
    steps = [_step(f"s{i}") for i in range(1, 10)]
    errors = validate_sop_plan(_plan(steps), _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert any(f"超过上限 {_MAX_STEPS}" in error for error in errors)


def test_bad_dependency_errors() -> None:
    plan = _valid_plan()
    plan.steps[1].dependencies = ["s1", "s99"]
    errors = validate_sop_plan(plan, _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert any("依赖不存在的步骤 's99'" in error for error in errors)


def test_self_dependency_errors() -> None:
    plan = _valid_plan()
    plan.steps[0].dependencies = ["s1"]
    errors = validate_sop_plan(plan, _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert any("不能依赖自身" in error for error in errors)


def test_cycle_errors() -> None:
    plan = _plan(
        [
            _step("s1", dependencies=["s2"]),
            _step("s2", dependencies=["s1"]),
        ]
    )
    errors = validate_sop_plan(plan, _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert any("循环依赖" in error for error in errors)


def test_unknown_assignee_errors() -> None:
    plan = _valid_plan()
    plan.steps[0].assignee = "team-ghost-role"
    errors = validate_sop_plan(plan, _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert any("不在团队花名册中" in error for error in errors)


def test_empty_expected_output_errors() -> None:
    plan = _valid_plan()
    plan.steps[0].expected_output = "   "
    errors = validate_sop_plan(plan, _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert any("expected_output 不能为空" in error for error in errors)


def test_below_min_steps_is_not_an_error() -> None:
    plan = _plan([_step("s1")])
    errors = validate_sop_plan(plan, _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert errors == []


def test_model_defaults() -> None:
    step = _step("s1")
    assert step.status.value == "pending"
    assert step.description == ""
    assert step.dependencies == []
    assert step.acceptance_criteria == []
    assert step.attempts == 0
    assert step.stage is None
    assert step.output is None
    assert step.error is None

    plan = _plan([step])
    assert plan.status == "draft"
    assert plan.summary == ""
    assert plan.user_feedback is None


def test_duplicate_and_blank_step_ids_are_rejected() -> None:
    plan = _plan([_step("s1"), _step("s1"), _step(" ")])
    errors = validate_sop_plan(plan, _ROSTER, _MAX_STEPS, _MIN_STEPS)
    assert any("不能重复" in error for error in errors)
    assert any("step_id 不能为空" in error for error in errors)


def test_configured_max_steps_cannot_exceed_hard_cap() -> None:
    steps = [_step(f"s{i}") for i in range(1, 14)]
    errors = validate_sop_plan(_plan(steps), _ROSTER, 100, _MIN_STEPS)
    assert any("上限 12" in error for error in errors)
