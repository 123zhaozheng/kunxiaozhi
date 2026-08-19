from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.routes import chat
from src.kernel.exceptions import SandboxCapacityUnavailable
from src.kernel.schemas.agent import AgentRequest
from src.kernel.schemas.user import TokenPayload


def test_chat_admission_is_before_run_generation_and_persistence() -> None:
    source = Path("src/api/routes/chat.py").read_text(encoding="utf-8")
    admission = source.index("Sandbox admission is intentionally before")
    run_generation = source.index("run_id = _generate_run_id()")
    assert admission < run_generation
    assert 'error": exc.code' in source
    assert "OpenSandboxCapacityUnavailable" in source


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", ["search", "team"])
async def test_capacity_rejection_happens_before_run_generation(
    monkeypatch: pytest.MonkeyPatch,
    agent_id: str,
) -> None:
    calls: list[str] = []

    async def resolve_persona(*_args, **_kwargs) -> None:
        return None

    async def validate_model(*_args, **_kwargs) -> None:
        return None

    class _Agent:
        _supports_sandbox = True

    class _Manager:
        async def admit(self, _user_id: str) -> None:
            calls.append("admit")
            raise SandboxCapacityUnavailable()

    def generate_run_id() -> str:
        calls.append("run_id")
        return "should-not-exist"

    monkeypatch.setattr(chat, "resolve_persona_request", resolve_persona)
    monkeypatch.setattr(chat, "validate_agent_model_access", validate_model)
    monkeypatch.setattr(chat, "get_task_manager", lambda: SimpleNamespace())
    monkeypatch.setattr(chat.AgentFactory, "get_class", lambda _agent_id: _Agent)
    monkeypatch.setattr(chat, "validate_team_agent_request", lambda *_args: None)
    monkeypatch.setattr(chat.settings, "SANDBOX_PLATFORM", "opensandbox")
    monkeypatch.setattr(
        "src.infra.sandbox.session_manager.get_session_sandbox_manager",
        lambda: _Manager(),
    )
    monkeypatch.setattr("src.infra.task.manager._generate_run_id", generate_run_id)

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    user = TokenPayload(sub="user-1", username="user-1", roles=[], permissions=[])

    with pytest.raises(HTTPException) as exc_info:
        await chat.chat_stream(AgentRequest(message="hello"), request, agent_id, user)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error": "sandbox_capacity_unavailable",
        "message": "当前用户太多，沙盒资源有限～请先切换到 Fast 模式继续聊，或稍后再试。",
    }
    assert calls == ["admit"]


@pytest.mark.asyncio
async def test_fast_agent_bypasses_opensandbox_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def resolve_persona(*_args, **_kwargs) -> None:
        return None

    async def validate_model(*_args, **_kwargs) -> None:
        return None

    class FastAgent:
        _supports_sandbox = False

    class Manager:
        async def admit(self, _user_id: str) -> None:
            calls.append("admit")

    class ReachedRunGenerationError(Exception):
        pass

    def generate_run_id() -> str:
        calls.append("run_id")
        raise ReachedRunGenerationError

    monkeypatch.setattr(chat, "resolve_persona_request", resolve_persona)
    monkeypatch.setattr(chat, "validate_agent_model_access", validate_model)
    monkeypatch.setattr(chat, "get_task_manager", lambda: SimpleNamespace())
    monkeypatch.setattr(chat.AgentFactory, "get_class", lambda _agent_id: FastAgent)
    monkeypatch.setattr(chat.settings, "SANDBOX_PLATFORM", "opensandbox")
    monkeypatch.setattr(
        "src.infra.sandbox.session_manager.get_session_sandbox_manager",
        lambda: Manager(),
    )
    monkeypatch.setattr("src.infra.task.manager._generate_run_id", generate_run_id)

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    user = TokenPayload(sub="user-1", username="user-1", roles=[], permissions=[])

    with pytest.raises(ReachedRunGenerationError):
        await chat.chat_stream(AgentRequest(message="hello"), request, "fast", user)

    assert calls == ["run_id"]
