"""present_team_event 白名单 + emit_team_event 双写测试。"""

from __future__ import annotations

import pytest

from src.infra.writer.present import Presenter
from src.infra.writer.presenter_config import PresenterConfig
from src.infra.writer.presenter_events import EventPresenterMixin


class _Host(EventPresenterMixin):
    """最小宿主：验证 present_team_event 白名单与事件结构（无 IO）。"""

    def __init__(self) -> None:
        self.config = PresenterConfig(session_id="session-1", agent_id="team")
        self.trace_id = "trace-1"
        self.run_id = "run-1"
        self._step_count = 0
        self._tool_calls = []


def test_present_team_event_whitelist_accepts_sop_events() -> None:
    host = _Host()

    updated = host.present_team_event("sop:updated", {"plan_id": "p1", "status": "running"})
    assert updated["event"] == "sop:updated"
    assert updated["data"]["plan_id"] == "p1"
    assert updated["data"]["status"] == "running"
    assert updated["data"]["agent_id"] == "team"

    generating = host.present_team_event("sop:plan_generating", {"goal": "g"})
    assert generating["event"] == "sop:plan_generating"

    approval = host.present_team_event("approval_required", {"id": "a1", "type": "sop_plan"})
    assert approval["event"] == "approval_required"


def test_present_team_event_rejects_unknown_types() -> None:
    host = _Host()
    with pytest.raises(ValueError, match="unsupported TeamAgent event type"):
        host.present_team_event("team:plan", {"plan_id": "p1"})


@pytest.mark.asyncio
async def test_emit_team_event_presents_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    presenter = Presenter(PresenterConfig(session_id="session-1", agent_id="team"))
    saved: list[dict] = []

    async def fake_save_event(event):
        saved.append(event)

    monkeypatch.setattr(presenter, "save_event", fake_save_event)

    event = await presenter.emit_team_event("sop:updated", {"plan_id": "p1"})

    assert event["event"] == "sop:updated"
    assert event["data"]["plan_id"] == "p1"
    # 双写：present 构建 + save_event 持久化同一事件
    assert saved == [event]
