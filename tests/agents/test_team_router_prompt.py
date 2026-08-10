"""build_team_router_system_prompt 测试：路由约束注入、SOP 条件子句、瘦身。"""

from __future__ import annotations

from src.agents.team_agent.prompt import build_team_router_system_prompt


class _FakeMember:
    member_id = "m1"
    role_name = "Researcher"
    role_avatar = ""
    role_instructions = None


class _FakeTeam:
    name = "Team A"
    team_instructions = ""
    default_member_id = "m1"
    active_members = [_FakeMember()]


def test_router_prompt_contains_routing_constraints() -> None:
    prompt = build_team_router_system_prompt(_FakeTeam(), default_role="general-purpose")
    assert "路由约束" in prompt
    # 主代理不亲自执行、文档指派子代理、文件交付归子代理
    assert "不直接读取" in prompt
    assert "reveal_file" in prompt
    assert "子代理" in prompt


def test_router_prompt_no_sop_clause_when_disabled() -> None:
    prompt = build_team_router_system_prompt(
        _FakeTeam(), default_role="general-purpose", sop_enabled=False
    )
    assert "update_sop" not in prompt


def test_router_prompt_has_sop_clause_when_enabled() -> None:
    prompt = build_team_router_system_prompt(
        _FakeTeam(), default_role="general-purpose", sop_enabled=True
    )
    assert "update_sop" in prompt
    assert "running" in prompt
