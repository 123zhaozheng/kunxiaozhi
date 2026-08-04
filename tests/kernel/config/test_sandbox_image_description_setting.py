"""Settings definitions for sandbox image description."""

from __future__ import annotations

from src.kernel.config import service as config_service
from src.kernel.config.base import Settings
from src.kernel.config.constants import RESTART_REQUIRED_SETTINGS
from src.kernel.config.definitions import SETTING_DEFINITIONS
from src.kernel.schemas.setting import SettingCategory, SettingType


def test_sandbox_image_description_field_and_definition() -> None:
    definition = SETTING_DEFINITIONS["SANDBOX_IMAGE_DESCRIPTION"]

    assert Settings(_env_file=None).SANDBOX_IMAGE_DESCRIPTION == ""
    assert definition["type"] is SettingType.TEXT
    assert definition["category"] is SettingCategory.SANDBOX
    assert definition["subcategory"] == "general"
    assert definition["default"] == ""
    assert definition["depends_on"] == "ENABLE_SANDBOX"
    assert definition.get("frontend_visible") is True
    assert definition["description"] == "settingDesc.SANDBOX_IMAGE_DESCRIPTION"


def test_sandbox_image_description_not_in_restart_or_manager_reset() -> None:
    assert "SANDBOX_IMAGE_DESCRIPTION" not in RESTART_REQUIRED_SETTINGS
    assert "SANDBOX_IMAGE_DESCRIPTION" not in config_service._SANDBOX_AFFECTED_SETTINGS
