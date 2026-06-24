"""Definitions for the stage-2 model-card ID settings + legacy triplet removal."""

from __future__ import annotations

from src.kernel.config.base import Settings
from src.kernel.config.definitions import SETTING_DEFINITIONS, SettingCategory, SettingType


def _assert_card_id_setting(key: str, category: SettingCategory) -> None:
    definition = SETTING_DEFINITIONS[key]

    assert definition["type"] == SettingType.STRING
    assert definition["category"] == category
    assert definition["default"] == ""
    assert definition.get("frontend_visible", False) is True


def test_native_memory_model_id_is_frontend_visible_card_reference() -> None:
    _assert_card_id_setting("NATIVE_MEMORY_MODEL_ID", SettingCategory.MEMORY_STORAGE)


def test_audio_transcription_model_id_is_frontend_visible_card_reference() -> None:
    _assert_card_id_setting(
        "AUDIO_TRANSCRIPTION_MODEL_ID", SettingCategory.AUDIO_TRANSCRIPTION
    )


def test_native_memory_embedding_model_id_is_frontend_visible_card_reference() -> None:
    _assert_card_id_setting(
        "NATIVE_MEMORY_EMBEDDING_MODEL_ID", SettingCategory.MEMORY_EMBEDDING
    )


def test_native_memory_rerank_model_id_is_frontend_visible_card_reference() -> None:
    _assert_card_id_setting("NATIVE_MEMORY_RERANK_MODEL_ID", SettingCategory.MEMORY_SEARCH)


def test_new_model_id_settings_default_to_empty_string() -> None:
    settings = Settings(_env_file=None)
    assert settings.NATIVE_MEMORY_MODEL_ID == ""
    assert settings.AUDIO_TRANSCRIPTION_MODEL_ID == ""
    assert settings.NATIVE_MEMORY_EMBEDDING_MODEL_ID == ""
    assert settings.NATIVE_MEMORY_RERANK_MODEL_ID == ""


def test_legacy_memory_storage_triplet_definitions_removed() -> None:
    for removed_key in (
        "NATIVE_MEMORY_MODEL",
        "NATIVE_MEMORY_API_BASE",
        "NATIVE_MEMORY_API_KEY",
    ):
        assert removed_key not in SETTING_DEFINITIONS
        assert not hasattr(Settings(_env_file=None), removed_key)


def test_legacy_memory_embedding_triplet_definitions_removed() -> None:
    for removed_key in (
        "NATIVE_MEMORY_EMBEDDING_API_BASE",
        "NATIVE_MEMORY_EMBEDDING_API_KEY",
        "NATIVE_MEMORY_EMBEDDING_MODEL",
    ):
        assert removed_key not in SETTING_DEFINITIONS
        assert not hasattr(Settings(_env_file=None), removed_key)


def test_legacy_memory_rerank_triplet_definitions_removed() -> None:
    for removed_key in (
        "NATIVE_MEMORY_RERANK_MODEL",
        "NATIVE_MEMORY_RERANK_API_BASE",
        "NATIVE_MEMORY_RERANK_API_KEY",
    ):
        assert removed_key not in SETTING_DEFINITIONS
        assert not hasattr(Settings(_env_file=None), removed_key)


def test_legacy_audio_transcription_triplet_definitions_removed() -> None:
    for removed_key in (
        "AUDIO_TRANSCRIPTION_API_KEY",
        "AUDIO_TRANSCRIPTION_BASE_URL",
        "AUDIO_TRANSCRIPTION_MODEL",
    ):
        assert removed_key not in SETTING_DEFINITIONS
        assert not hasattr(Settings(_env_file=None), removed_key)
