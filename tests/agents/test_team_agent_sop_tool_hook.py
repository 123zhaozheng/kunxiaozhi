"""nodes.py TEAM_SOP_MODE 开关测试。

开启且显式团队模式时：主代理 filtered_tools 含 update_sop、_prompt_sections 含
SOP 引导段；关闭时行为与现状一致（tools=None，不含 update_sop）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakeDeepAgent:
    def __init__(self) -> None:
        self.captured_create_kwargs = None

    def with_config(self, _config):
        return self

    async def astream_events(self, _initial_state, _config, version="v2"):
        if False:
            yield version


class _FakeEventProcessor:
    def __init__(self, *_args, **_kwargs) -> None:
        self.output_text = ""

    async def process_event(self, _event) -> None:
        return None

    async def flush(self) -> None:
        return None

    def clear(self) -> None:
        return None


def _patch_common(monkeypatch: pytest.MonkeyPatch, module, fake_graph: _FakeDeepAgent) -> None:
    async def fake_get_model(**_kwargs):
        return object()

    async def fake_resolve_fallback_model(*_args, **_kwargs):
        return None

    async def fake_checkpointer(**_kwargs):
        return object()

    async def fake_store():
        return object()

    async def fake_emit_token_usage(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module.LLMClient, "get_model", fake_get_model)
    monkeypatch.setattr(module, "resolve_fallback_model", fake_resolve_fallback_model)
    monkeypatch.setattr(module, "get_async_checkpointer", fake_checkpointer)
    monkeypatch.setattr(module, "acreate_store", fake_store)
    monkeypatch.setattr(module, "emit_token_usage", fake_emit_token_usage)
    monkeypatch.setattr(module, "AgentEventProcessor", _FakeEventProcessor)

    def fake_create_deep_agent(**kwargs):
        fake_graph.captured_create_kwargs = kwargs
        return fake_graph

    monkeypatch.setattr(module, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(module, "create_retry_middleware", lambda **_kwargs: [])
    monkeypatch.setattr(module, "ToolResultBinaryMiddleware", lambda **_kwargs: object())
    monkeypatch.setattr(module, "SubagentActivityMiddleware", lambda **_kwargs: object())
    monkeypatch.setattr(module, "PromptCachingMiddleware", lambda: object())
    monkeypatch.setattr(module.settings, "ENABLE_MCP", False)
    monkeypatch.setattr(module.settings, "ENABLE_MEMORY", False)
    monkeypatch.setattr(module.settings, "ENABLE_SKILLS", False)
    monkeypatch.setattr(module.settings, "ENABLE_RECOMMEND_QUESTIONS", False)


def _install_deepagents_shims(monkeypatch: pytest.MonkeyPatch) -> None:
    import deepagents

    monkeypatch.setattr(
        deepagents,
        "HarnessProfile",
        lambda **kwargs: SimpleNamespace(**kwargs),
        raising=False,
    )
    monkeypatch.setattr(
        deepagents,
        "register_harness_profile",
        lambda *_args, **_kwargs: None,
        raising=False,
    )


class _FakeMember:
    member_id = "m1"
    role_name = "Researcher"
    role_avatar = ""
    role_instructions = None
    persona_preset_id = "preset-1"


class _FakeTeam:
    name = "Team A"
    team_instructions = ""
    default_member_id = "m1"
    active_members = [_FakeMember()]


class _FakePreset:
    system_prompt = "You are a researcher. You research thoroughly."
    skill_names = []


class _TeamManager:
    async def resolve_team_for_runtime(self, team_id: str, *, owner_user_id: str):
        return _FakeTeam()


class _PresetManager:
    async def use_preset(self, preset_id: str, *, user_id: str, is_admin: bool = False):
        return _FakePreset()


async def _setup_node(monkeypatch: pytest.MonkeyPatch, *, sop_mode: bool) -> _FakeDeepAgent:
    from src.agents.team_agent import nodes as team_nodes
    from src.agents.team_agent.context import TeamAgentContext
    from src.infra.persona_preset import manager as preset_manager_module
    from src.infra.team import manager as team_manager_module

    fake_graph = _FakeDeepAgent()
    _patch_common(monkeypatch, team_nodes, fake_graph)
    _install_deepagents_shims(monkeypatch)

    monkeypatch.setattr(team_nodes.settings, "ENABLE_SANDBOX", False)
    monkeypatch.setattr(team_nodes.settings, "TEAM_SOP_MODE", sop_mode)
    monkeypatch.setattr(team_manager_module, "get_team_manager", lambda: _TeamManager())
    monkeypatch.setattr(
        preset_manager_module,
        "get_persona_preset_manager",
        lambda: _PresetManager(),
    )
    monkeypatch.setattr(
        team_nodes,
        "create_persistent_backend_factory",
        lambda **_kwargs: object(),
    )

    context = TeamAgentContext(session_id="session-1", user_id="user-1")

    async def fake_setup():
        return None

    async def fake_close():
        return None

    monkeypatch.setattr(context, "setup", fake_setup)
    monkeypatch.setattr(context, "close", fake_close)

    config = {
        "configurable": {
            "context": context,
            "presenter": object(),
            "base_url": "",
            "agent_options": {},
            "team_id": "team-1",
        }
    }

    await team_nodes.team_router_node(
        {"input": "hello", "session_id": "session-1", "attachments": []},
        config,
    )
    return fake_graph


@pytest.mark.asyncio
async def test_sop_mode_enabled_adds_update_sop_tool_and_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_graph = await _setup_node(monkeypatch, sop_mode=True)

    tools = fake_graph.captured_create_kwargs["tools"]
    assert tools is not None
    assert any(getattr(tool, "name", None) == "update_sop" for tool in tools)
    # DeepAgents 0.6.7 inherits parent tools when a subagent omits `tools`;
    # every child must therefore receive the explicit non-router tool list.
    subagents = fake_graph.captured_create_kwargs["subagents"]
    assert subagents
    assert all(
        "update_sop" not in {getattr(tool, "name", "") for tool in (subagent.get("tools") or [])}
        for subagent in subagents
    )

    # 主代理 middleware 含 SOP 引导段
    from src.infra.agent.middleware.prompt_injection import SectionPromptMiddleware

    sections: list[str] = []
    for middleware in fake_graph.captured_create_kwargs["middleware"]:
        if isinstance(middleware, SectionPromptMiddleware):
            sections.extend(middleware._sections)
    assert any("SOP" in section for section in sections)


@pytest.mark.asyncio
async def test_sop_mode_disabled_leaves_tools_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_graph = await _setup_node(monkeypatch, sop_mode=False)

    # 开关关闭：MCP 关闭时 filtered_tools 保持 None，与现状一致
    assert fake_graph.captured_create_kwargs["tools"] is None


@pytest.mark.asyncio
async def test_sop_mode_mounts_dispatch_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEAM_SOP_MODE 开启：主代理 middleware 含 SopDispatchGuardMiddleware。"""
    from src.agents.team_agent.sop.guard import SopDispatchGuardMiddleware

    fake_graph = await _setup_node(monkeypatch, sop_mode=True)
    middleware = fake_graph.captured_create_kwargs["middleware"]
    assert any(isinstance(mw, SopDispatchGuardMiddleware) for mw in middleware)


@pytest.mark.asyncio
async def test_sop_mode_disabled_no_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEAM_SOP_MODE 关闭：主代理 middleware 不含 SopDispatchGuardMiddleware。"""
    from src.agents.team_agent.sop.guard import SopDispatchGuardMiddleware

    fake_graph = await _setup_node(monkeypatch, sop_mode=False)
    middleware = fake_graph.captured_create_kwargs["middleware"]
    assert not any(isinstance(mw, SopDispatchGuardMiddleware) for mw in middleware)


@pytest.mark.asyncio
async def test_team_mode_strips_file_guides_from_main_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """团队模式：主代理 prompt sections 不含 reveal_file / 文件与工作区 / 工具发现引导。"""
    from src.infra.agent.middleware.prompt_injection import SectionPromptMiddleware

    fake_graph = await _setup_node(monkeypatch, sop_mode=True)
    sections: list[str] = []
    for middleware in fake_graph.captured_create_kwargs["middleware"]:
        if isinstance(middleware, SectionPromptMiddleware):
            sections.extend(middleware._sections)
    joined = "\n".join(sections)
    assert "reveal_file" not in joined
    assert "文件交付" not in joined
    assert "工具选择" not in joined
    # 保留安全栅栏
    assert "高风险操作" in joined or "验证" in joined


@pytest.mark.asyncio
async def test_sop_guidance_uses_strict_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """SOP 引导段使用强制措辞（'必须'/'不可跳过'）。"""
    from src.infra.agent.middleware.prompt_injection import SectionPromptMiddleware

    fake_graph = await _setup_node(monkeypatch, sop_mode=True)
    sections: list[str] = []
    for middleware in fake_graph.captured_create_kwargs["middleware"]:
        if isinstance(middleware, SectionPromptMiddleware):
            sections.extend(middleware._sections)
    sop_section = next(s for s in sections if "SOP" in s)
    assert "必须" in sop_section
    assert "不可跳过" in sop_section
