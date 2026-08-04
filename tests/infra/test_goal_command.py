from __future__ import annotations

from src.infra.goal import (
    GoalSpec,
    build_default_rubric,
    build_goal_input,
    build_goal_prompt_section,
    create_goal_rubric_middleware,
)


def test_build_default_rubric_mentions_objective() -> None:
    rubric = build_default_rubric("ship the dashboard")

    assert "ship the dashboard" in rubric
    assert "verified" in rubric


def test_build_goal_prompt_section_matches_traditional_goal_behavior() -> None:
    goal = GoalSpec(objective="migrate billing", rubric="- billing migrated")

    section = build_goal_prompt_section(goal.model_dump())

    assert "当前目标" in section
    assert "migrate billing" in section
    assert "不得判定完成" in section
    assert "每条显式要求" in section


def test_create_goal_rubric_middleware_uses_installed_deepagents_support() -> None:
    goal = GoalSpec(objective="migrate billing", rubric="- billing migrated")

    middleware = create_goal_rubric_middleware(model="openai:gpt-4o-mini", goal=goal)

    assert middleware is not None


def test_create_goal_rubric_middleware_is_optional_when_deepagents_lacks_it(
    monkeypatch,
) -> None:
    goal = GoalSpec(objective="migrate billing", rubric="- billing migrated")

    monkeypatch.setattr("src.infra.goal._load_rubric_middleware_class", lambda: None)

    assert create_goal_rubric_middleware(model="openai:gpt-4o-mini", goal=goal) is None


def test_build_goal_input_adds_rubric_only_when_middleware_is_available() -> None:
    goal = GoalSpec(objective="migrate billing", rubric="- billing migrated")

    without_rubric = build_goal_input("message", goal, rubric_middleware=None)
    with_rubric = build_goal_input("message", goal, rubric_middleware=object())

    assert without_rubric == {"messages": ["message"]}
    assert with_rubric == {
        "messages": ["message"],
        "rubric": goal.rubric,
    }
