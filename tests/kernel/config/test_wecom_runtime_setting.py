from src.kernel.config.base import Settings
from src.kernel.config.constants import RESTART_REQUIRED_SETTINGS
from src.kernel.config.definitions import SETTING_DEFINITIONS


def test_wecom_runtime_mode_defaults_to_embedded() -> None:
    assert Settings(_env_file=None).WECOM_RUNTIME_MODE == "embedded"


def test_wecom_runtime_mode_is_backend_only_restart_setting() -> None:
    definition = SETTING_DEFINITIONS["WECOM_RUNTIME_MODE"]

    assert definition["default"] == "embedded"
    assert definition["options"] == ["embedded", "external", "disabled"]
    assert definition["frontend_visible"] is False
    assert "WECOM_RUNTIME_MODE" in RESTART_REQUIRED_SETTINGS
