"""ScheduledRunMiddleware：定时运行时过滤用户交互类工具并拦截误调用，以及 nodes 接线。"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from src.agents.core.scheduled_run import (
    SCHEDULED_RUN_BLOCKED_MESSAGE,
    SCHEDULED_RUN_EXCLUDED_TOOLS,
    SCHEDULED_RUN_PROMPT,
    SCHEDULED_TASK_CHAT_TOOL_NAMES,
    ScheduledRunMiddleware,
    is_scheduled_run,
    scheduled_run_middleware,
    scheduled_run_prompt_sections,
)
from src.infra.logging.context import TraceContext

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_context():
    TraceContext.clear_request_context()
    yield
    TraceContext.clear_request_context()


def _make_tool(name: str):
    @tool(name)
    def _t(_arg: str = "") -> str:
        """test tool"""

        return "ok"

    return _t


def _request(tools: list) -> ModelRequest:
    return ModelRequest(
        model=ChatOpenAI(model="gpt-scheduled-test", api_key="test"),
        messages=[],
        system_message=SystemMessage(content="sys"),
        tools=tools,
    )


def _names(request: ModelRequest) -> list[str]:
    return [t["name"] if isinstance(t, dict) else t.name for t in request.tools]


def test_constants() -> None:
    assert SCHEDULED_TASK_CHAT_TOOL_NAMES == {"list_scheduled_tasks", "draft_scheduled_task"}
    assert SCHEDULED_RUN_EXCLUDED_TOOLS == {
        "ask_human",
        "update_sop",
        "save_to_knowledge_base",
        "list_scheduled_tasks",
        "draft_scheduled_task",
    }
    assert SCHEDULED_RUN_PROMPT.startswith("## 定时任务自动执行")
    assert "需要你确认的事项" in SCHEDULED_RUN_PROMPT


def test_filters_excluded_tools_and_keeps_the_rest() -> None:
    tools = [
        _make_tool(n)
        for n in (
            "ask_human",
            "update_sop",
            "list_scheduled_tasks",
            "draft_scheduled_task",
            "execute",
            "read_file",
        )
    ]
    filtered = ScheduledRunMiddleware()._override(_request(tools))

    assert _names(filtered) == ["execute", "read_file"]


def test_filters_dict_tools_by_name() -> None:
    tools = [{"name": "ask_human"}, {"name": "web_search"}, _make_tool("update_sop")]
    filtered = ScheduledRunMiddleware()._override(_request(tools))

    assert _names(filtered) == ["web_search"]


def test_returns_request_unchanged_when_nothing_to_filter() -> None:
    request = _request([_make_tool("execute"), _make_tool("read_file")])

    assert ScheduledRunMiddleware()._override(request) is request


def test_wrap_model_call_passes_filtered_request_to_handler() -> None:
    request = _request([_make_tool("ask_human"), _make_tool("execute")])
    seen: list[ModelRequest] = []

    def handler(req):
        seen.append(req)
        return "response"

    assert ScheduledRunMiddleware().wrap_model_call(request, handler) == "response"
    assert _names(seen[0]) == ["execute"]


def test_awrap_model_call_passes_filtered_request_to_handler() -> None:
    request = _request([_make_tool("update_sop"), _make_tool("execute")])
    seen: list[ModelRequest] = []

    async def handler(req):
        seen.append(req)
        return "response"

    result = asyncio.run(ScheduledRunMiddleware().awrap_model_call(request, handler))

    assert result == "response"
    assert _names(seen[0]) == ["execute"]


def _tool_request(name: str, call_id: str = "call-1"):
    return SimpleNamespace(tool_call={"name": name, "args": {}, "id": call_id})


@pytest.mark.parametrize("name", sorted(SCHEDULED_RUN_EXCLUDED_TOOLS))
def test_blocked_tool_call_returns_message_without_calling_handler(name: str) -> None:
    calls: list[object] = []

    async def handler(req):
        calls.append(req)
        return "executed"

    result = asyncio.run(ScheduledRunMiddleware().awrap_tool_call(_tool_request(name), handler))

    assert calls == []
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call-1"
    assert result.name == name
    payload = json.loads(result.content)
    assert payload == {"status": "unavailable", "message": SCHEDULED_RUN_BLOCKED_MESSAGE}


def test_sync_blocked_tool_call_returns_message_without_calling_handler() -> None:
    calls: list[object] = []

    def handler(req):
        calls.append(req)
        return "executed"

    result = ScheduledRunMiddleware().wrap_tool_call(_tool_request("ask_human"), handler)

    assert calls == []
    assert isinstance(result, ToolMessage)
    assert "用户不在场" in result.content


def test_other_tool_calls_pass_through() -> None:
    request = _tool_request("execute")

    async def handler(req):
        assert req is request
        return "executed"

    assert asyncio.run(ScheduledRunMiddleware().awrap_tool_call(request, handler)) == "executed"
    assert ScheduledRunMiddleware().wrap_tool_call(request, lambda r: "executed") == "executed"


def test_real_agent_graph_hides_and_blocks_excluded_tools() -> None:
    """真实 create_agent 图：模型绑定的工具里没有 ask_human，误调用时不执行工具，只返回提示。"""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    executed: list[str] = []
    bound: list[list[str]] = []

    @tool
    def ask_human(q: str) -> str:
        """ask"""
        executed.append("ask_human")
        return "answer"

    @tool
    def execute(c: str) -> str:
        """run"""
        executed.append("execute")
        return "done"

    class _Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            bound.append([t["name"] if isinstance(t, dict) else t.name for t in tools])
            return self

    replies = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "ask_human", "args": {"q": "x"}, "id": "c1"},
                    {"name": "execute", "args": {"c": "y"}, "id": "c2"},
                ],
            ),
            AIMessage(content="final"),
        ]
    )
    agent = create_agent(
        _Model(messages=replies), tools=[ask_human, execute], middleware=[ScheduledRunMiddleware()]
    )

    result = asyncio.run(agent.ainvoke({"messages": [HumanMessage("hi")]}))

    assert executed == ["execute"]
    assert bound and all(names == ["execute"] for names in bound)
    tool_messages = {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}
    assert json.loads(tool_messages["c1"].content)["status"] == "unavailable"
    assert tool_messages["c2"].content == "done"


def test_helpers_follow_the_unattended_flag() -> None:
    assert is_scheduled_run() is False
    assert scheduled_run_prompt_sections() == []
    assert scheduled_run_middleware() == []

    TraceContext.set_request_context(unattended=True)

    assert is_scheduled_run() is True
    assert scheduled_run_prompt_sections() == [SCHEDULED_RUN_PROMPT]
    middlewares = scheduled_run_middleware()
    assert len(middlewares) == 1
    assert isinstance(middlewares[0], ScheduledRunMiddleware)

    TraceContext.set_request_context(unattended=False)
    assert scheduled_run_prompt_sections() == []
    assert scheduled_run_middleware() == []


# ── nodes wiring (AST) ───────────────────────────────────────────────────────

NODE_FILES = {
    "fast": REPO_ROOT / "src/agents/fast_agent/nodes.py",
    "search": REPO_ROOT / "src/agents/search_agent/nodes.py",
    "team": REPO_ROOT / "src/agents/team_agent/nodes.py",
}


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return None


def _list_op(stmt: ast.stmt) -> tuple[str, str, str | None] | None:
    """``target.extend(f())`` / ``target.append(F(...))`` -> (target, method, inner call name)."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return None
    func = stmt.value.func
    if not (isinstance(func, ast.Attribute) and func.attr in {"extend", "append"}):
        return None
    if not stmt.value.args:
        return None
    return ast.unparse(func.value), func.attr, _call_name(stmt.value.args[0])


def _statement_lists(tree: ast.AST):
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list) and stmts and isinstance(stmts[0], ast.stmt):
                yield stmts


def _adjacent_pairs(path: Path) -> list[tuple[str, int, int]]:
    """(target, extend_lineno, caching_lineno) for each scheduled_run_middleware() extend that is
    immediately followed by ``target.append(PromptCachingMiddleware())`` (ignoring comments)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pairs = []
    for stmts in _statement_lists(tree):
        for current, following in zip(stmts, stmts[1:]):
            op = _list_op(current)
            nxt = _list_op(following)
            if (
                op
                and nxt
                and op[1:] == ("extend", "scheduled_run_middleware")
                and nxt[1:] == ("append", "PromptCachingMiddleware")
                and op[0] == nxt[0]
            ):
                pairs.append((op[0], current.lineno, following.lineno))
    return pairs


@pytest.mark.parametrize("agent", ["fast", "search", "team"])
def test_nodes_mount_middleware_right_before_prompt_caching(agent: str) -> None:
    path = NODE_FILES[agent]
    pairs = _adjacent_pairs(path)
    targets = sorted(t for t, _, _ in pairs)
    main_stack = "user_middleware"
    sub_stack = "subagent_middleware" if agent != "team" else "mw"
    # 主代理栈与子代理栈都必须挂载，且紧邻在 PromptCachingMiddleware 之前
    assert targets == sorted([main_stack, sub_stack]), pairs

    # 整个模块里 scheduled_run_middleware() 的调用恰好就是这两处
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if _call_name(n) == "scheduled_run_middleware"]
    assert len(calls) == 2


@pytest.mark.parametrize("agent", ["fast", "search", "team"])
def test_nodes_add_prompt_sections_before_section_middleware(agent: str) -> None:
    path = NODE_FILES[agent]
    tree = ast.parse(path.read_text(encoding="utf-8"))

    section_calls = [n for n in ast.walk(tree) if _call_name(n) == "scheduled_run_prompt_sections"]
    assert section_calls, f"{agent} nodes must call scheduled_run_prompt_sections()"

    # 主代理：`_prompt_sections.extend(...)` 必须先于 SectionPromptMiddleware(sections=_prompt_sections)
    extend_lines = [
        s.lineno
        for stmts in _statement_lists(tree)
        for s in stmts
        if (_list_op(s) or ("",))[0] == "_prompt_sections"
        and _list_op(s)[1:] == ("extend", "scheduled_run_prompt_sections")
    ]
    assert len(extend_lines) == 1
    main_section_mw = [
        n.lineno
        for n in ast.walk(tree)
        if _call_name(n) == "SectionPromptMiddleware"
        and any(
            kw.arg == "sections" and ast.unparse(kw.value) == "_prompt_sections"
            for kw in n.keywords
        )
    ]
    assert main_section_mw and extend_lines[0] < min(main_section_mw)

    if agent in {"fast", "search"}:
        sub_extend = [
            s.lineno
            for stmts in _statement_lists(tree)
            for s in stmts
            if (_list_op(s) or ("",))[0] == "subagent_prompt_sections"
            and _list_op(s)[1:] == ("extend", "scheduled_run_prompt_sections")
        ]
        assert len(sub_extend) == 1
        sub_section_mw = [
            n.lineno
            for n in ast.walk(tree)
            if _call_name(n) == "SectionPromptMiddleware"
            and any(
                kw.arg == "sections" and ast.unparse(kw.value) == "subagent_prompt_sections"
                for kw in n.keywords
            )
        ]
        assert sub_section_mw and sub_extend[0] < min(sub_section_mw)
