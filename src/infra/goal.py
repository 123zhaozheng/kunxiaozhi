"""Run-scoped goal prompt and rubric helpers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GoalSpec(BaseModel):
    """A run-scoped goal with rubric criteria."""

    objective: str = Field(..., min_length=1, description="The goal to pursue")
    rubric: str = Field(..., min_length=1, description="Completion criteria")
    max_iterations: int = Field(3, ge=1, le=20, description="Rubric iteration cap")


def build_default_rubric(objective: str) -> str:
    """Build a conservative default rubric for a goal objective."""
    return "\n".join(
        [
            f"- The final result directly satisfies this objective: {objective}",
            "- Every explicit requirement from the user has been addressed.",
            "- The work is verified with the strongest relevant evidence available.",
            "- Any remaining uncertainty, limitation, or skipped verification is clearly reported.",
        ]
    )


def coerce_goal_spec(value: object) -> GoalSpec | None:
    """Return a GoalSpec from API or metadata data if possible."""
    if isinstance(value, GoalSpec):
        return value
    if isinstance(value, dict):
        try:
            return GoalSpec.model_validate(value)
        except Exception:
            return None
    return None


def build_goal_prompt_section(goal: dict | GoalSpec | None) -> str:
    """Render the active goal as a system prompt section."""
    spec = coerce_goal_spec(goal)
    if spec is None:
        return ""

    return (
        "## 当前目标\n"
        f"目标：{spec.objective}\n\n"
        "完成标准：\n"
        f"{spec.rubric}\n\n"
        "逐轮推进直至完成标准全部满足；每条显式要求须对照当前证据核实。"
        "证据不足以证明目标与完成标准均已达成前，不得判定完成。"
    )


def _load_rubric_middleware_class():
    """Return DeepAgents RubricMiddleware when installed by the current version."""
    for module_name, attr_name in (
        ("deepagents", "RubricMiddleware"),
        ("deepagents.middleware", "RubricMiddleware"),
        ("deepagents.middleware.rubric", "RubricMiddleware"),
    ):
        try:
            module = __import__(module_name, fromlist=[attr_name])
            middleware_cls = getattr(module, attr_name, None)
        except Exception:
            middleware_cls = None
        if middleware_cls is not None:
            return middleware_cls
    return None


def create_goal_rubric_middleware(*, model: object, goal: dict | GoalSpec | None):
    """Create RubricMiddleware when the installed DeepAgents version supports it."""
    spec = coerce_goal_spec(goal)
    if spec is None:
        return None

    middleware_cls = _load_rubric_middleware_class()
    if middleware_cls is None:
        return None

    try:
        return middleware_cls(model=model, max_iterations=spec.max_iterations)
    except TypeError:
        try:
            return middleware_cls(model=model)
        except Exception:
            return None
    except Exception:
        return None


def build_goal_input(
    new_message: object,
    goal: dict | GoalSpec | None,
    *,
    rubric_middleware: object | None,
) -> dict[str, object]:
    """Build DeepAgent input, adding rubric only when middleware will consume it."""
    payload: dict[str, object] = {"messages": [new_message]}
    spec = coerce_goal_spec(goal)
    if spec is not None and rubric_middleware is not None:
        payload["rubric"] = spec.rubric
    return payload
