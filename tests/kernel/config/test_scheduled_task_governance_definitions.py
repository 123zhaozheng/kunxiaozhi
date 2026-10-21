from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.infra.auth.rbac import RBACManager
from src.kernel.config.base import Settings
from src.kernel.config.definitions import SETTING_DEFINITIONS
from src.kernel.schemas.permission import PERMISSION_METADATA, get_permissions_response
from src.kernel.schemas.scheduled_task import (
    ScheduledTask,
    ScheduledTaskCreate,
    ScheduledTaskResponse,
    ScheduledTaskUpdate,
)
from src.kernel.types import Permission

NEW_SETTINGS = {
    "SCHEDULED_TASK_CHAT_TOOL_ENABLED": (True, None, None),
    "SCHEDULED_TASK_MAX_CONCURRENT_RUNS": (10, 1, 100),
    "SCHEDULED_TASK_QUEUE_TIMEOUT_MINUTES": (20, 1, 120),
}


def test_scheduled_task_permissions_are_defined_grouped_and_labelled() -> None:
    assert Permission.SCHEDULED_TASK_USE.value == "scheduled_task:use"
    assert Permission.SCHEDULED_TASK_ADMIN.value == "scheduled_task:admin"
    assert PERMISSION_METADATA["scheduled_task:use"]["label"] == "使用定时任务"
    assert PERMISSION_METADATA["scheduled_task:admin"]["label"] == "管理所有定时任务"

    grouped = {
        p.value
        for group in get_permissions_response().groups
        if group.name == "定时任务"
        for p in group.permissions
    }
    assert grouped == {"scheduled_task:use", "scheduled_task:admin"}


def test_default_roles_grant_use_to_user_only() -> None:
    roles = {role["name"]: role for role in RBACManager().get_default_roles()}
    assert "scheduled_task:use" in roles["user"]["permissions"]
    assert "scheduled_task:admin" not in roles["user"]["permissions"]
    assert "scheduled_task:use" not in roles["guest"]["permissions"]
    assert "scheduled_task:admin" not in roles["guest"]["permissions"]
    assert {"scheduled_task:use", "scheduled_task:admin"} <= set(roles["admin"]["permissions"])


@pytest.mark.parametrize("key,expected", NEW_SETTINGS.items())
def test_new_scheduled_task_settings(key, expected) -> None:
    default, minimum, maximum = expected
    definition = SETTING_DEFINITIONS[key]
    assert definition["default"] == default
    assert getattr(Settings(), key) == default
    assert definition.get("minimum") == minimum
    assert definition.get("maximum") == maximum
    assert definition["depends_on"] == "SCHEDULED_TASK_ENABLED"
    assert definition["subcategory"] == "scheduled_task"
    assert definition.get("frontend_visible", False) is False


def test_existing_scheduled_task_settings_have_ranges() -> None:
    per_user = SETTING_DEFINITIONS["SCHEDULED_TASK_MAX_PER_USER"]
    interval = SETTING_DEFINITIONS["SCHEDULED_TASK_MIN_INTERVAL_HOURS"]
    assert (per_user["minimum"], per_user["maximum"]) == (1, 100)
    assert (interval["minimum"], interval["maximum"]) == (1, 24)
    # the master switch stays visible to the frontend
    assert SETTING_DEFINITIONS["SCHEDULED_TASK_ENABLED"]["frontend_visible"] is True


def test_every_scheduled_task_setting_has_description_in_zh_and_en() -> None:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "frontend" / "src" / "i18n" / "locales"
    for lang in ("zh", "en"):
        desc = json.loads((root / f"{lang}.json").read_text(encoding="utf-8"))["settingDesc"]
        for key in SETTING_DEFINITIONS:
            if key.startswith("SCHEDULED_TASK_"):
                assert desc.get(key), f"{lang} settingDesc.{key} missing"


class TestScheduledTaskSchemas:
    base = {
        "name": "晨报",
        "prompt": "整理",
        "schedule": {"kind": "daily", "time": "08:00"},
    }

    def test_agent_id_is_restricted(self) -> None:
        assert ScheduledTaskCreate(**self.base).agent_id == "fast"
        assert ScheduledTaskCreate(**self.base, agent_id="search").agent_id == "search"
        with pytest.raises(ValidationError):
            ScheduledTaskCreate(**self.base, agent_id="team")
        with pytest.raises(ValidationError):
            ScheduledTaskUpdate(agent_id="team")

    def test_blank_persona_becomes_none(self) -> None:
        assert ScheduledTaskCreate(**self.base, persona_preset_id="").persona_preset_id is None
        assert ScheduledTaskCreate(**self.base, persona_preset_id="  ").persona_preset_id is None
        update = ScheduledTaskUpdate(persona_preset_id="")
        assert update.model_dump(exclude_unset=True) == {"persona_preset_id": None}
        assert ScheduledTaskUpdate(persona_preset_id="p1").persona_preset_id == "p1"

    def test_legacy_document_reads_with_defaults(self) -> None:
        task = ScheduledTask.model_validate(
            {
                "_id": "t",
                "owner_id": "u",
                "name": "n",
                "prompt": "p",
                "schedule": {"kind": "daily", "time": "09:00"},
                "agent_id": "legacy-agent",
            }
        )
        assert task.paused_by is None
        assert task.created_via == "web"
        assert task.agent_id == "legacy-agent"

    def test_response_exposes_new_fields(self) -> None:
        fields = ScheduledTaskResponse.model_fields
        for name in (
            "paused_by",
            "created_via",
            "persona_name",
            "persona_avatar",
            "persona_available",
        ):
            assert name in fields
        assert fields["persona_name"].default is None
        assert fields["persona_available"].default is None
        assert fields["created_via"].default == "web"
