from __future__ import annotations

from src.kernel.config.base import Settings
from src.kernel.config.definitions import SETTING_DEFINITIONS
from src.kernel.schemas.setting import SettingType


def test_oa_sso_timeout_default_matches_setting_definition() -> None:
    definition = SETTING_DEFINITIONS["OA_SSO_TIMEOUT_SECONDS"]

    assert Settings().OA_SSO_TIMEOUT_SECONDS == 60.0
    assert definition["default"] == 60.0
    assert definition["type"] == SettingType.NUMBER
    assert definition["depends_on"] == "OA_SSO_ENABLED"
