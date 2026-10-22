"""Registration / exclusion / localization of the scheduled task chat tools."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from src.agents.core.harness_prompt_overrides import (
    DEFAULT_HARNESS_CATALOG,
    localize_tool_for_model,
)
from src.agents.core.scheduled_run import SCHEDULED_TASK_CHAT_TOOL_NAMES
from src.agents.team_agent.context import TEAM_ROUTER_EXCLUDED_TOOLS
from src.infra.tool import internal_registry
from src.infra.tool import scheduled_task_tool as tool_mod

_CJK = re.compile(r"[\u4e00-\u9fff]")
_NAMES = {"list_scheduled_tasks", "draft_scheduled_task"}


def _build_names(monkeypatch: pytest.MonkeyPatch, **flags: bool) -> set[str]:
    settings = internal_registry.settings
    for key, value in {
        "ENABLE_SANDBOX": False,
        "ENABLE_IMAGE_GENERATION": False,
        "ENABLE_AUDIO_TRANSCRIPTION": False,
        "ENABLE_DOCUMENT_PARSE": False,
        "DIFY_KB_ENABLED": False,
        "SCHEDULED_TASK_ENABLED": True,
        "SCHEDULED_TASK_CHAT_TOOL_ENABLED": True,
        **flags,
    }.items():
        monkeypatch.setattr(settings, key, value, raising=False)
    return {t.name for t in internal_registry.build_internal_tools()}


def test_tool_names_match_scheduled_run_exclusion_list() -> None:
    assert {tool_mod.LIST_TOOL_NAME, tool_mod.DRAFT_TOOL_NAME} == _NAMES
    assert SCHEDULED_TASK_CHAT_TOOL_NAMES == _NAMES
    assert {t.name for t in tool_mod.get_scheduled_task_tools()} == _NAMES


def test_registered_when_both_switches_on(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _NAMES <= _build_names(monkeypatch)


@pytest.mark.parametrize(
    "flags",
    [{"SCHEDULED_TASK_ENABLED": False}, {"SCHEDULED_TASK_CHAT_TOOL_ENABLED": False}],
)
def test_not_registered_when_a_switch_is_off(
    monkeypatch: pytest.MonkeyPatch, flags: dict[str, bool]
) -> None:
    assert not (_NAMES & _build_names(monkeypatch, **flags))


def test_team_router_excludes_both_tools() -> None:
    assert _NAMES <= TEAM_ROUTER_EXCLUDED_TOOLS


def test_wecom_submit_disables_draft_tool() -> None:
    source = Path("src/infra/agent/wecom/handler.py").read_text(encoding="utf-8")
    submits = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "submit"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "task_manager"
    ]
    assert submits, "task_manager.submit(...) call not found in the WeCom handler"
    for call in submits:
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        value = kwargs.get("disabled_tools")
        assert isinstance(value, ast.List), "WeCom submit must pass disabled_tools"
        assert [e.id for e in value.elts if isinstance(e, ast.Name)] == [
            "DRAFT_TOOL_NAME",
            "KB_SAVE_TOOL_NAME",
        ]
    assert tool_mod.DRAFT_TOOL_NAME == "draft_scheduled_task"


def _all_property_descriptions(schema: dict) -> dict[str, str | None]:
    found: dict[str, str | None] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            for name, prop in (node.get("properties") or {}).items():
                found[name] = prop.get("description") if isinstance(prop, dict) else None
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return found


def _model_view_schema(tool) -> dict:
    """Schema the model actually receives after harness localization.

    Tools with an injected ``runtime`` argument pass through ``localize_tool_for_model``
    unchanged (like every other runtime-injected internal tool), so the model sees the
    tool's own ``tool_call_schema``; otherwise the localized ``args_schema`` dict.
    """
    localized = localize_tool_for_model(tool, DEFAULT_HARNESS_CATALOG)
    if isinstance(localized.args_schema, dict):
        return localized.args_schema
    return localized.tool_call_schema.model_json_schema()


def test_localization_keeps_chinese_descriptions_for_nested_schedule_fields() -> None:
    from src.infra.tool.mcp_client import MCPToolWithRetry

    draft = next(t for t in tool_mod.get_scheduled_task_tools() if t.name == "draft_scheduled_task")
    # both the raw tool and the MCPToolWithRetry wrapper the agents actually receive
    for candidate in (draft, MCPToolWithRetry(draft)):
        localized = localize_tool_for_model(candidate, DEFAULT_HARNESS_CATALOG)
        assert "只生成草稿" in localized.description and "确认" in localized.description
        assert "不支持删除" in localized.description and "now" in localized.description

        descriptions = _all_property_descriptions(_model_view_schema(candidate))
        expected = {
            "action",
            "task_id",
            "task_name",
            "name",
            "prompt",
            "schedule",
            "persona_name",
            "clear_persona",
            "enabled",
            "kind",
            "time",
            "weekdays",
            "month_day",
            "run_at",
            "every_hours",
        }
        assert expected <= set(descriptions)
        for field in expected:
            assert descriptions[field] and _CJK.search(descriptions[field]), field
        assert "runtime" not in descriptions
        assert descriptions["time"] == "执行时刻 HH:MM，北京时间。"


def test_harness_catalog_registers_both_tools() -> None:
    # Spec: every model-visible built-in is registered in the catalog in the same change.
    assert {"list_scheduled_tasks", "draft_scheduled_task"} <= set(
        DEFAULT_HARNESS_CATALOG.tool_descriptions
    )
    assert {"list_scheduled_tasks", "draft_scheduled_task"} <= set(
        DEFAULT_HARNESS_CATALOG.schema_fields
    )


def test_list_tool_is_localized_without_parameters() -> None:
    listing = next(
        t for t in tool_mod.get_scheduled_task_tools() if t.name == "list_scheduled_tasks"
    )
    localized = localize_tool_for_model(listing, DEFAULT_HARNESS_CATALOG)
    assert _CJK.search(localized.description)
    assert _all_property_descriptions(_model_view_schema(listing)) == {}
