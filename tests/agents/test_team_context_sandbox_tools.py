"""TeamAgentContext.setup() 沙箱工具补齐测试。

沙箱开启时注册 upload_url_to_sandbox，关闭时不注册；
既有工具（ask_human 等）加载不受影响。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _fake_tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _install_fast_agent_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock FastAgentContext.setup 的 DB 依赖，让真实 setup 可离线运行。"""
    from src.agents.fast_agent import context as fast_context
    from src.infra.mcp import quota as quota_module
    from src.kernel.config import settings

    monkeypatch.setattr(
        fast_context, "get_human_tool", lambda *_args, **_kwargs: _fake_tool("ask_human")
    )
    monkeypatch.setattr(
        fast_context, "get_reveal_file_tool", lambda: _fake_tool("reveal_file")
    )
    monkeypatch.setattr(
        fast_context, "get_reveal_project_tool", lambda: _fake_tool("reveal_project")
    )
    monkeypatch.setattr(
        fast_context, "get_transfer_file_tool", lambda: _fake_tool("transfer_file")
    )
    monkeypatch.setattr(
        fast_context, "get_transfer_path_tool", lambda: _fake_tool("transfer_path")
    )

    async def _fake_get_internal_tools_for_user(*_args, **_kwargs):
        # 模拟内部工具：含应裁剪的管理/市场工具 + 保留的通用工具
        return [
            _fake_tool("find_skills"),
            _fake_tool("install_skill"),
            _fake_tool("create_persona_preset"),
            _fake_tool("update_persona_preset"),
            _fake_tool("search_persona_presets"),
            _fake_tool("create_agent_team"),
            _fake_tool("read_document"),
        ]

    monkeypatch.setattr(
        fast_context, "get_internal_tools_for_user", _fake_get_internal_tools_for_user
    )

    async def _fake_resolve_user_mcp_access(*_args, **_kwargs):
        return [], False

    monkeypatch.setattr(
        quota_module, "resolve_user_mcp_access", _fake_resolve_user_mcp_access
    )

    monkeypatch.setattr(settings, "ENABLE_SKILLS", False)
    monkeypatch.setattr(settings, "ENABLE_MEMORY", False)
    monkeypatch.setattr(settings, "ENABLE_MCP", False)


def _tool_names(context) -> list[str]:
    return [getattr(tool, "name", None) for tool in context.tools]


@pytest.mark.asyncio
async def test_team_context_adds_upload_url_tool_when_sandbox_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fast_agent_mocks(monkeypatch)

    from src.kernel.config import settings

    monkeypatch.setattr(settings, "ENABLE_SANDBOX", True)

    from src.agents.team_agent.context import TeamAgentContext

    context = TeamAgentContext(session_id="session-1", user_id="user-1")
    await context.setup()

    names = _tool_names(context)
    assert "upload_url_to_sandbox" in names
    assert "ask_human" not in names


@pytest.mark.asyncio
async def test_team_context_skips_upload_url_tool_when_sandbox_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fast_agent_mocks(monkeypatch)

    from src.kernel.config import settings

    monkeypatch.setattr(settings, "ENABLE_SANDBOX", False)

    from src.agents.team_agent.context import TeamAgentContext

    context = TeamAgentContext(session_id="session-1", user_id="user-1")
    await context.setup()

    names = _tool_names(context)
    assert "upload_url_to_sandbox" not in names
    assert "ask_human" not in names


@pytest.mark.asyncio
async def test_team_context_excludes_router_irrelevant_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """团队主代理裁剪路由无关工具，保留通用干活工具。"""
    _install_fast_agent_mocks(monkeypatch)

    from src.kernel.config import settings

    monkeypatch.setattr(settings, "ENABLE_SANDBOX", True)

    from src.agents.team_agent.context import TeamAgentContext

    context = TeamAgentContext(session_id="session-1", user_id="user-1")
    await context.setup()

    names = _tool_names(context)
    # 应裁剪：人工交互 / 技能市场 / persona 管理 / 团队管理
    for excluded in (
        "ask_human",
        "find_skills",
        "install_skill",
        "create_persona_preset",
        "update_persona_preset",
        "search_persona_presets",
        "create_agent_team",
    ):
        assert excluded not in names, f"expected {excluded} to be excluded"
    # 保留：通用工具 + 沙箱上传 + 文档解析
    for kept in (
        "reveal_file",
        "reveal_project",
        "transfer_file",
        "transfer_path",
        "read_document",
        "upload_url_to_sandbox",
    ):
        assert kept in names, f"expected {kept} to be kept"

