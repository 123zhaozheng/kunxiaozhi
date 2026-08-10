"""Size and contract tests for the default Chinese concise harness."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from typing import Any

import pytest
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool, tool

from src.agents.core.harness_prompt_overrides import (
    DEFAULT_HARNESS_BEHAVIOR_GUIDE,
    DEFAULT_HARNESS_CATALOG,
    VENDOR_AVAILABLE_AGENTS_HEADING,
    HarnessLocalizationMiddleware,
    build_default_harness_profile,
    build_harness_extra_middleware,
    localize_tool_for_model,
)


def _without_annotations(value: Any) -> Any:
    if isinstance(value, list):
        return [_without_annotations(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _without_annotations(item)
            for key, item in value.items()
            if key not in {"description", "title"}
        }
    return value


def _builtin_tools() -> list[BaseTool]:
    return [*FilesystemMiddleware().tools, *TodoListMiddleware().tools]


def test_default_harness_catalog_contracts() -> None:
    assert "{available_agents}" in DEFAULT_HARNESS_CATALOG.tool_descriptions["task"]
    assert len(DEFAULT_HARNESS_CATALOG.tool_descriptions["task"]) < 600
    assert len(DEFAULT_HARNESS_CATALOG.tool_descriptions["write_todos"]) < 300
    assert len(DEFAULT_HARNESS_CATALOG.write_todos_system) < 200


def test_default_harness_uses_chinese_human_guidance() -> None:
    catalog = DEFAULT_HARNESS_CATALOG
    assert "执行" in catalog.tool_descriptions["task"]
    assert "复杂多步" in catalog.tool_descriptions["write_todos"]
    assert "文件规则" in catalog.filesystem_system
    assert "Current task start time" in catalog.tool_descriptions["task"]


def test_default_profile_keeps_one_native_todo_with_localized_model_view() -> None:
    profile = build_default_harness_profile()
    assert TodoListMiddleware not in profile.excluded_middleware
    assert len(profile.materialize_extra_middleware()) == 1

    todo = TodoListMiddleware()
    todo_tool = next(item for item in todo.tools if item.name == "write_todos")
    localized = localize_tool_for_model(todo_tool, DEFAULT_HARNESS_CATALOG)
    assert localized.description == DEFAULT_HARNESS_CATALOG.tool_descriptions["write_todos"]
    assert "恰好一项 in_progress" in localized.description
    assert "禁止并行调用" in localized.description


def test_model_view_preserves_middleware_rendered_tool_descriptions() -> None:
    from deepagents.backends import StateBackend
    from deepagents.middleware.subagents import SubAgentMiddleware
    from langchain_openai import ChatOpenAI

    catalog = DEFAULT_HARNESS_CATALOG
    middleware = SubAgentMiddleware(
        backend=StateBackend(),
        subagents=[
            {
                "name": "researcher",
                "description": "Find facts",
                "system_prompt": "Test agent.",
                "tools": [],
                "model": ChatOpenAI(model="test", api_key="test"),
            }
        ],
        task_description=catalog.tool_descriptions["task"],
    )
    rendered_task = next(item for item in middleware.tools if item.name == "task")
    localized = localize_tool_for_model(rendered_task, catalog)
    assert localized.description == rendered_task.description
    assert "researcher" in localized.description
    assert "{available_agents}" not in localized.description


def test_vendor_system_prompt_snapshots_are_pinned() -> None:
    import inspect

    from deepagents.middleware.filesystem import (
        EXECUTION_SYSTEM_PROMPT,
        FILESYSTEM_SYSTEM_PROMPT,
    )
    from deepagents.middleware.subagents import TASK_SYSTEM_PROMPT, SubAgentMiddleware
    from langchain.agents.middleware.todo import WRITE_TODOS_SYSTEM_PROMPT

    expected = {
        "filesystem": "017d28a83d0e389385274fa29bfaaaba8567e9e71d899314bc61ae77369f6092",
        "execute": "7f1082b1dca0563378cd0a41f3b139b6f6fb46e5b485f1c2d3460896551c5c2c",
        "task": "efc798167c4614ff3bd46aff5fa592468c6cd9acbb181f7e4cfd0618c02ee9ca",
        "todo": "f5ac422b1b71a61b9a6d1367d93fa7bd6671c42e9ac63c593aec98a8e2ff7f7a",
    }
    actual = {
        "filesystem": hashlib.sha256(FILESYSTEM_SYSTEM_PROMPT.encode()).hexdigest(),
        "execute": hashlib.sha256(EXECUTION_SYSTEM_PROMPT.encode()).hexdigest(),
        "task": hashlib.sha256(TASK_SYSTEM_PROMPT.encode()).hexdigest(),
        "todo": hashlib.sha256(WRITE_TODOS_SYSTEM_PROMPT.encode()).hexdigest(),
    }
    assert actual == expected
    assert VENDOR_AVAILABLE_AGENTS_HEADING in inspect.getsource(
        SubAgentMiddleware.__init__
    )


def test_schema_localization_preserves_machine_contract() -> None:
    catalog = DEFAULT_HARNESS_CATALOG
    for original in _builtin_tools():
        localized = localize_tool_for_model(original, catalog)
        assert isinstance(localized, BaseTool)
        assert localized is not original
        assert original.args_schema is not localized.args_schema
        original_schema = original.get_input_schema().model_json_schema()
        assert _without_annotations(localized.args_schema) == _without_annotations(
            original_schema
        )
        serialized = json.dumps(localized.args_schema, ensure_ascii=False)
        assert "Input schema for" not in serialized
        assert "Absolute path" not in serialized


def test_search_tools_schema_and_description_are_localized() -> None:
    from src.infra.tool.tool_search_tool import ToolSearchTool

    original = ToolSearchTool(manager=object())  # type: ignore[arg-type]
    catalog = DEFAULT_HARNESS_CATALOG
    localized = localize_tool_for_model(original, catalog)

    assert localized.description == catalog.tool_descriptions["search_tools"]
    assert "能力关键词" in localized.args_schema["properties"]["query"]["description"]
    assert _without_annotations(localized.args_schema) == _without_annotations(
        original.get_input_schema().model_json_schema()
    )


def test_schema_localization_preserves_cache_extras_and_unknown_tools() -> None:
    catalog = DEFAULT_HARNESS_CATALOG
    source = _builtin_tools()[0].model_copy(
        update={"extras": {"cache_control": {"type": "ephemeral"}, "x": 1}}
    )
    localized = localize_tool_for_model(source, catalog)
    assert localized.extras == source.extras
    assert source.get_input_schema().model_json_schema()["properties"]["path"][
        "description"
    ].startswith("Absolute")

    @tool
    def custom_tool(value: str) -> str:
        """Third-party tool that must pass through unchanged."""
        return value

    assert localize_tool_for_model(custom_tool, catalog) is custom_tool


def test_original_pydantic_schemas_still_enforce_required_defaults_and_enums() -> None:
    tools = {item.name: item for item in _builtin_tools()}

    read_input = tools["read_file"].get_input_schema().model_validate(
        {"file_path": "/workspace/example.txt"}
    )
    assert read_input.offset == 0
    assert read_input.limit == 100
    with pytest.raises(Exception):
        tools["read_file"].get_input_schema().model_validate({})

    grep_schema = tools["grep"].get_input_schema()
    assert grep_schema.model_validate({"pattern": "x"}).output_mode == "files_with_matches"
    with pytest.raises(Exception):
        grep_schema.model_validate({"pattern": "x", "output_mode": "invalid"})


def test_default_harness_tools_are_smaller_than_native_vendor_schemas() -> None:
    catalog = DEFAULT_HARNESS_CATALOG
    originals = _builtin_tools()
    native_payload = [
        {
            "name": item.name,
            "description": item.description,
            "schema": item.get_input_schema().model_json_schema(),
        }
        for item in originals
    ]
    compact_payload = [
        {
            "name": item.name,
            "description": catalog.tool_descriptions.get(item.name, item.description),
            "schema": localize_tool_for_model(item, catalog).args_schema,
        }
        for item in originals
    ]
    native_chars = len(json.dumps(native_payload, ensure_ascii=False))
    compact_chars = len(json.dumps(compact_payload, ensure_ascii=False))
    assert compact_chars < native_chars * 0.65


def test_extra_middleware_contains_todo_and_localizer() -> None:
    middleware = list(build_harness_extra_middleware())
    assert len(middleware) == 1
    assert isinstance(middleware[0], HarnessLocalizationMiddleware)


def test_localizer_rewrites_final_model_request_without_replacing_runtime_tools() -> None:
    from deepagents.middleware.filesystem import (
        EXECUTION_SYSTEM_PROMPT,
        FILESYSTEM_SYSTEM_PROMPT,
    )
    from deepagents.middleware.subagents import TASK_SYSTEM_PROMPT
    from langchain.agents.middleware.todo import WRITE_TODOS_SYSTEM_PROMPT
    from langchain_openai import ChatOpenAI

    original = _builtin_tools()[0]
    vendor_system = "\n\n".join(
        (
            FILESYSTEM_SYSTEM_PROMPT,
            EXECUTION_SYSTEM_PROMPT,
            TASK_SYSTEM_PROMPT,
            WRITE_TODOS_SYSTEM_PROMPT,
            "Available subagent types:\n- researcher: Find facts",
        )
    )
    request = ModelRequest(
        model=ChatOpenAI(model="gpt-harness-test", api_key="test"),
        messages=[],
        system_message=SystemMessage(content=vendor_system),
        tools=[original],
    )
    localized = HarnessLocalizationMiddleware(DEFAULT_HARNESS_CATALOG)._override(
        request
    )

    assert "文件规则" in localized.system_message.text
    assert "在沙箱运行命令" in localized.system_message.text
    assert "仅委派隔离且复杂的工作" in localized.system_message.text
    assert "可用代理类型：" in localized.system_message.text
    assert "Following Conventions" not in localized.system_message.text
    assert "Execute Tool" not in localized.system_message.text
    assert "subagent spawner" not in localized.system_message.text
    assert "Available subagent types:" not in localized.system_message.text
    assert DEFAULT_HARNESS_CATALOG.write_todos_system in localized.system_message.text
    assert "The `write_todos` tool should never be called multiple times" not in localized.system_message.text
    assert localized.tools[0] is not original
    assert original.get_input_schema().model_json_schema()["properties"]["path"][
        "description"
    ].startswith("Absolute")


def test_shared_profile_resolves_for_supported_adapters() -> None:
    from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model
    from langchain_anthropic import ChatAnthropic
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI

    models = (
        ChatAnthropic(model_name="claude-harness-test", api_key="test"),
        ChatOpenAI(model="gpt-harness-test", api_key="test"),
        ChatGoogleGenerativeAI(model="gemini-harness-test", google_api_key="test"),
    )
    for model in models:
        profile = _harness_profile_for_model(model, None)
        assert profile.base_system_prompt == DEFAULT_HARNESS_BEHAVIOR_GUIDE
        assert "{available_agents}" in profile.tool_description_overrides["task"]
        assert TodoListMiddleware not in profile.excluded_middleware
        assert len(profile.materialize_extra_middleware()) == 1


def test_default_harness_boots_in_isolated_process() -> None:
    script = """
import json
from src.agents.core.harness_prompt_overrides import DEFAULT_HARNESS_BEHAVIOR_GUIDE
from src.agents.fast_agent.prompt import FAST_SYSTEM_PROMPT
print(json.dumps({
    "behavior": DEFAULT_HARNESS_BEHAVIOR_GUIDE,
    "fast": FAST_SYSTEM_PROMPT,
}, ensure_ascii=False))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    assert "工作闭环" in result["behavior"]
    assert len(result["fast"]) < 500


def test_deferred_manager_can_be_imported_first_without_cycle() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import src.infra.tool.deferred_manager"],
        cwd=os.getcwd(),
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
