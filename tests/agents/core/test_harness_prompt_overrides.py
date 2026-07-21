"""Mode, size, and contract tests for the reversible harness."""

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
    VENDOR_AVAILABLE_AGENTS_HEADING,
    HarnessLocalizationMiddleware,
    build_harness_extra_middleware,
    build_short_todo_middleware,
    catalog_for_mode,
    localize_tool_for_model,
)
from src.kernel.config import normalize_harness_mode


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


def test_mode_validation_and_catalogs() -> None:
    assert normalize_harness_mode(" Compact_ZH ") == "compact_zh"
    with pytest.raises(ValueError, match="AGENT_HARNESS_MODE"):
        normalize_harness_mode("broken")

    for mode in ("compact_en", "compact_zh"):
        catalog = catalog_for_mode(mode)
        assert "{available_agents}" in catalog.tool_descriptions["task"]
        assert len(catalog.tool_descriptions["task"]) < 600
        assert len(catalog.tool_descriptions["write_todos"]) < 300
        assert len(catalog.write_todos_system) < 200


def test_mode_setting_is_typed_visible_and_restart_required() -> None:
    from pydantic import ValidationError

    from src.kernel.config.base import Settings
    from src.kernel.config.constants import RESTART_REQUIRED_SETTINGS
    from src.kernel.config.definitions import SETTING_DEFINITIONS, SettingType

    definition = SETTING_DEFINITIONS["AGENT_HARNESS_MODE"]
    assert definition["type"] is SettingType.SELECT
    assert definition["default"] == "compact_zh"
    assert definition["options"] == ["legacy", "compact_en", "compact_zh"]
    assert (
        Settings(_env_file=None, AGENT_HARNESS_MODE=" Compact_ZH ").AGENT_HARNESS_MODE
        == "compact_zh"
    )
    assert "AGENT_HARNESS_MODE" in RESTART_REQUIRED_SETTINGS
    with pytest.raises(ValidationError):
        Settings(_env_file=None, AGENT_HARNESS_MODE="broken")


def test_compact_zh_uses_chinese_human_guidance() -> None:
    catalog = catalog_for_mode("compact_zh")
    assert "执行" in catalog.tool_descriptions["task"]
    assert "复杂多步" in catalog.tool_descriptions["write_todos"]
    assert "文件规则" in catalog.filesystem_system
    assert "Current task start time" in catalog.tool_descriptions["task"]


def test_todo_replacement_survives_exact_type_exclusion() -> None:
    from deepagents import HarnessProfile
    from deepagents._excluded_middleware import _apply_excluded_middleware

    compact = build_short_todo_middleware("compact_zh")
    assert len(compact) == 1
    assert type(compact[0]) is not TodoListMiddleware
    remaining = _apply_excluded_middleware(
        [TodoListMiddleware(), compact[0]],
        HarnessProfile(excluded_middleware=frozenset({TodoListMiddleware})),
    )
    assert remaining == compact
    assert "write_todos" in {item.name for item in compact[0].tools}
    todo_tool = next(item for item in compact[0].tools if item.name == "write_todos")
    expected = catalog_for_mode("compact_zh").tool_descriptions["write_todos"]
    assert todo_tool.description == expected
    assert "恰好一项 in_progress" in todo_tool.description
    assert "禁止并行调用" in todo_tool.description
    assert "恰好一项 in_progress" in compact[0].system_prompt
    assert "禁止并行调用" in compact[0].system_prompt


def test_model_view_preserves_middleware_rendered_tool_descriptions() -> None:
    from deepagents.backends import StateBackend
    from deepagents.middleware.subagents import SubAgentMiddleware
    from langchain_openai import ChatOpenAI

    catalog = catalog_for_mode("compact_zh")
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

    expected = {
        "filesystem": "017d28a83d0e389385274fa29bfaaaba8567e9e71d899314bc61ae77369f6092",
        "execute": "7f1082b1dca0563378cd0a41f3b139b6f6fb46e5b485f1c2d3460896551c5c2c",
        "task": "efc798167c4614ff3bd46aff5fa592468c6cd9acbb181f7e4cfd0618c02ee9ca",
    }
    actual = {
        "filesystem": hashlib.sha256(FILESYSTEM_SYSTEM_PROMPT.encode()).hexdigest(),
        "execute": hashlib.sha256(EXECUTION_SYSTEM_PROMPT.encode()).hexdigest(),
        "task": hashlib.sha256(TASK_SYSTEM_PROMPT.encode()).hexdigest(),
    }
    assert actual == expected
    assert VENDOR_AVAILABLE_AGENTS_HEADING in inspect.getsource(
        SubAgentMiddleware.__init__
    )


def test_schema_localization_preserves_machine_contract() -> None:
    catalog = catalog_for_mode("compact_zh")
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
    catalog = catalog_for_mode("compact_zh")
    localized = localize_tool_for_model(original, catalog)

    assert localized.description == catalog.tool_descriptions["search_tools"]
    assert "能力关键词" in localized.args_schema["properties"]["query"]["description"]
    assert _without_annotations(localized.args_schema) == _without_annotations(
        original.get_input_schema().model_json_schema()
    )


def test_schema_localization_preserves_cache_extras_and_unknown_tools() -> None:
    catalog = catalog_for_mode("compact_zh")
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


def test_compact_tools_are_smaller_than_native_vendor_schemas() -> None:
    catalog = catalog_for_mode("compact_zh")
    originals = _builtin_tools()
    legacy_payload = [
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
    legacy_chars = len(json.dumps(legacy_payload, ensure_ascii=False))
    compact_chars = len(json.dumps(compact_payload, ensure_ascii=False))
    assert compact_chars < legacy_chars * 0.65


def test_extra_middleware_contains_todo_and_localizer() -> None:
    middleware = list(build_harness_extra_middleware("compact_zh"))
    assert len(middleware) == 2
    assert type(middleware[0]) is not TodoListMiddleware
    assert isinstance(middleware[1], HarnessLocalizationMiddleware)
    assert build_harness_extra_middleware("legacy") == ()


def test_localizer_rewrites_final_model_request_without_replacing_runtime_tools() -> None:
    from deepagents.middleware.filesystem import (
        EXECUTION_SYSTEM_PROMPT,
        FILESYSTEM_SYSTEM_PROMPT,
    )
    from deepagents.middleware.subagents import TASK_SYSTEM_PROMPT
    from langchain_openai import ChatOpenAI

    original = _builtin_tools()[0]
    vendor_system = "\n\n".join(
        (
            FILESYSTEM_SYSTEM_PROMPT,
            EXECUTION_SYSTEM_PROMPT,
            TASK_SYSTEM_PROMPT,
            "Available subagent types:\n- researcher: Find facts",
        )
    )
    request = ModelRequest(
        model=ChatOpenAI(model="gpt-harness-test", api_key="test"),
        messages=[],
        system_message=SystemMessage(content=vendor_system),
        tools=[original],
    )
    localized = HarnessLocalizationMiddleware(catalog_for_mode("compact_zh"))._override(
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
    assert localized.tools[0] is not original
    assert original.get_input_schema().model_json_schema()["properties"]["path"][
        "description"
    ].startswith("Absolute")


def test_shared_profile_resolves_for_supported_adapters() -> None:
    from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model
    from langchain_anthropic import ChatAnthropic
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI

    import src.agents.core.persona as persona

    models = (
        ChatAnthropic(model_name="claude-harness-test", api_key="test"),
        ChatOpenAI(model="gpt-harness-test", api_key="test"),
        ChatGoogleGenerativeAI(model="gemini-harness-test", google_api_key="test"),
    )
    for model in models:
        profile = _harness_profile_for_model(model, None)
        assert profile.base_system_prompt == persona._BEHAVIOR_GUIDE
        assert "{available_agents}" in profile.tool_description_overrides["task"]
        assert len(profile.materialize_extra_middleware()) == 2


def test_three_modes_boot_in_isolated_processes() -> None:
    script = """
import json
from src.kernel.config import settings
import src.agents.core.persona as persona
from src.agents.fast_agent.prompt import FAST_SYSTEM_PROMPT
print(json.dumps({
    "mode": settings.AGENT_HARNESS_MODE,
    "behavior": persona._BEHAVIOR_GUIDE,
    "fast": FAST_SYSTEM_PROMPT,
}, ensure_ascii=False))
"""
    results: dict[str, dict[str, str]] = {}
    for mode in ("legacy", "compact_en", "compact_zh"):
        env = os.environ.copy()
        env["AGENT_HARNESS_MODE"] = mode
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        results[mode] = json.loads(completed.stdout.strip().splitlines()[-1])

    assert results["compact_zh"]["mode"] == "compact_zh"
    assert "工作闭环" in results["compact_zh"]["behavior"]
    assert "Be concise" in results["compact_en"]["behavior"]
    assert "Core Behavior" in results["legacy"]["behavior"]
    assert len(results["compact_zh"]["fast"]) < len(results["legacy"]["fast"])


def test_deferred_manager_can_be_imported_first_without_cycle() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import src.infra.tool.deferred_manager as dm; print(dm._HARNESS_MODE)",
        ],
        cwd=os.getcwd(),
        env={**os.environ, "AGENT_HARNESS_MODE": "compact_zh"},
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip().splitlines()[-1] == "compact_zh"
