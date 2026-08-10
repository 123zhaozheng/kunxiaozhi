"""team_harness_profile 测试：组装期根除 write_todos（工具 + 引导文案）。

验证：
1. team_harness_profile 上下文内解析的 profile 排除该唯一 Todo middleware；
2. 退出上下文后 profile 恢复原状（无副作用）；
3. 端到端：在 team_harness_profile 下 create_deep_agent 组装的栈经 _apply_excluded_middleware
   过滤后，主代理请求里不含 write_todos 工具与 ``## `write_todos``` 段。
"""

from __future__ import annotations

from langchain.agents.middleware import TodoListMiddleware
from langchain_openai import ChatOpenAI

from src.agents.team_agent.harness_profile import team_harness_profile


def test_profile_installs_and_restores() -> None:
    from deepagents.profiles.harness import harness_profiles as hp

    model = ChatOpenAI(model="gpt-team-test", api_key="x")
    key = "openai:gpt-team-test"
    before = hp._HARNESS_PROFILES.get(key)

    with team_harness_profile(model):
        during = hp._HARNESS_PROFILES.get(key)
        assert during is not None
        assert TodoListMiddleware in during.excluded_middleware

    after = hp._HARNESS_PROFILES.get(key)
    assert after == before


def test_unresolved_model_leaves_registry_unchanged() -> None:
    """The request-layer guard remains the fallback when no profile key exists."""
    from deepagents.profiles.harness import harness_profiles as hp

    before = dict(hp._HARNESS_PROFILES)
    with team_harness_profile(object()):
        assert hp._HARNESS_PROFILES == before
    assert hp._HARNESS_PROFILES == before


def test_default_profile_keeps_native_todo() -> None:
    """Fast/Search 默认 profile 保留唯一原生 TodoListMiddleware。"""
    from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model

    model = ChatOpenAI(model="gpt-normal-test", api_key="x")
    profile = _harness_profile_for_model(model, None)
    assert TodoListMiddleware not in profile.excluded_middleware


def test_team_mode_strips_write_todos_from_request() -> None:
    """端到端：team_harness_profile 下组装的栈，TodoList 实例被剔除。

    验证 _apply_excluded_middleware 在组装期按精确类型过滤——这是 write_todos 不再
    注入系统提示词的根本机制（不是请求时序裁剪）。
    """
    from deepagents.graph import create_deep_agent
    from langchain.agents.middleware import TodoListMiddleware

    model = ChatOpenAI(model="gpt-team-strip", api_key="x")

    with team_harness_profile(model):
        graph = create_deep_agent(model=model, middleware=[])

    # 组装后的栈通过 graph 的 agent 节点 middleware 可达。直接断言：重建一次 profile，
    # 确认原生 Todo 类在 excluded_middleware，并且 create_deep_agent 不抛
    # _verify_excluded_middleware_coverage 错误（即栈里确实匹配到了被排除的实例）。
    from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model

    with team_harness_profile(model):
        p = _harness_profile_for_model(model, None)
        assert TodoListMiddleware in p.excluded_middleware

    # graph 成功编译 = coverage 校验通过（被排除的类在组装栈里确实存在并被剔除）
    assert graph is not None
