"""TeamToolExclusionMiddleware 测试：write_todos 工具与提示段在团队模式全链路不可见。"""

from __future__ import annotations

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from src.agents.core.harness_prompt_overrides import (
    DEFAULT_HARNESS_CATALOG,
    WRITE_TODOS_TOOL_NAME,
)
from src.agents.team_agent.tool_exclusion import (
    TeamToolExclusionMiddleware,
    _strip_write_todos_section,
)

_DEFAULT_LOCALIZED_SYSTEM = f"""## 行为
- 简洁直达。

## `{WRITE_TODOS_TOOL_NAME}`
{DEFAULT_HARNESS_CATALOG.write_todos_system}

## 团队
按角色能力分派实际工作。
"""

_LEGACY_SYSTEM = f"""## {WRITE_TODOS_TOOL_NAME}
Only use for complex multi-step work. Keep exactly one item in_progress.

## 团队
按角色能力分派实际工作。
"""


@tool
def write_todos(_todos: str) -> str:
    """Track multi-step work."""

    return "ok"


@tool
def execute(_command: str) -> str:
    """Run a shell command."""

    return "ok"


def _request(system: str, tools: list | None = None) -> ModelRequest:
    return ModelRequest(
        model=ChatOpenAI(model="gpt-exclusion-test", api_key="test"),
        messages=[],
        system_message=SystemMessage(content=system),
        tools=tools if tools is not None else [write_todos, execute],
    )


def _names(request: ModelRequest) -> list[str]:
    return [getattr(t, "name", "") for t in request.tools]


def test_strips_write_todos_tool_and_default_localized_section() -> None:
    request = _request(_DEFAULT_LOCALIZED_SYSTEM)
    filtered = TeamToolExclusionMiddleware()._override(request)

    assert WRITE_TODOS_TOOL_NAME not in _names(filtered)
    assert "execute" in _names(filtered)
    text = filtered.system_message.text
    assert f"## `{WRITE_TODOS_TOOL_NAME}`" not in text
    assert "禁止并行调用" not in text
    assert "## 行为" in text
    assert "## 团队" in text


def test_strips_legacy_heading_without_backticks() -> None:
    request = _request(_LEGACY_SYSTEM)
    filtered = TeamToolExclusionMiddleware()._override(request)

    text = filtered.system_message.text
    assert f"## {WRITE_TODOS_TOOL_NAME}" not in text
    assert "Only use for complex multi-step work" not in text
    assert "## 团队" in text


def test_handles_block_content_system_message() -> None:
    request = ModelRequest(
        model=ChatOpenAI(model="gpt-exclusion-test", api_key="test"),
        messages=[],
        system_message=SystemMessage(
            content=[
                {"type": "text", "text": "## 行为\n- 简洁直达。"},
                {
                    "type": "text",
                    "text": f"## `{WRITE_TODOS_TOOL_NAME}`\n{DEFAULT_HARNESS_CATALOG.write_todos_system}",
                },
                {"type": "text", "text": "## 团队\n按角色分派。"},
            ]
        ),
        tools=[write_todos, execute],
    )
    filtered = TeamToolExclusionMiddleware()._override(request)

    texts = [b["text"] for b in filtered.system_message.content]
    assert f"## `{WRITE_TODOS_TOOL_NAME}`" not in texts
    assert DEFAULT_HARNESS_CATALOG.write_todos_system not in texts
    assert any("## 行为" in t for t in texts)
    assert any("## 团队" in t for t in texts)
    assert WRITE_TODOS_TOOL_NAME not in _names(filtered)


def test_noop_when_nothing_to_strip() -> None:
    request = _request("## 行为\n- 简洁直达。", tools=[execute])
    filtered = TeamToolExclusionMiddleware()._override(request)

    assert filtered is request
    assert filtered.system_message is request.system_message
    assert filtered.tools is request.tools


def test_wrap_model_call_forwards_filtered_request() -> None:
    request = _request(_DEFAULT_LOCALIZED_SYSTEM)
    seen: list[ModelRequest] = []

    def handler(req: ModelRequest) -> str:
        seen.append(req)
        return "done"

    result = TeamToolExclusionMiddleware().wrap_model_call(request, handler)

    assert result == "done"
    assert len(seen) == 1
    assert WRITE_TODOS_TOOL_NAME not in _names(seen[0])
    assert f"## `{WRITE_TODOS_TOOL_NAME}`" not in seen[0].system_message.text


def test_strip_section_keeps_other_sections_intact() -> None:
    stripped = _strip_write_todos_section(
        f"头部\n\n## A\n甲\n\n## `{WRITE_TODOS_TOOL_NAME}`\n乙\n\n## B\n丙"
    )
    assert "## A" in stripped and "甲" in stripped
    assert "## B" in stripped and "丙" in stripped
    assert WRITE_TODOS_TOOL_NAME not in stripped and "乙" not in stripped
