from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.kernel.config import service as config_service


class _FakeSettingItem(SimpleNamespace):
    pass


class _FakeSettingsStorage:
    def __init__(
        self,
        legacy_value: object = "claude-3-5-haiku-20241022",
        legacy_updated: bool = True,
        new_id_value: str = "",
        new_id_updated: bool = False,
    ) -> None:
        self._legacy_value = legacy_value
        self._legacy_updated = legacy_updated
        self._new_id_value = new_id_value
        self._new_id_updated = new_id_updated
        self.set_calls: list[tuple[str, object, str]] = []

    async def get_raw(self, key: str):
        if key == "SESSION_TITLE_MODEL":
            if self._legacy_value is None and not self._legacy_updated:
                return None
            return _FakeSettingItem(
                key=key,
                value=self._legacy_value,
                updated_at="2026-01-01T00:00:00" if self._legacy_updated else None,
            )
        if key == "SESSION_TITLE_MODEL_ID":
            return _FakeSettingItem(
                key=key,
                value=self._new_id_value,
                updated_at="2026-01-01T00:00:00" if self._new_id_updated else None,
            )
        return None

    async def set(self, key: str, value, user_id: str):
        self.set_calls.append((key, value, user_id))
        return _FakeSettingItem(key=key, value=value, updated_at="2026-01-01T00:00:00")


class _FakeSettingsService:
    def __init__(self, storage: _FakeSettingsStorage) -> None:
        self._storage = storage


class _FakeModelStorage:
    def __init__(self, by_value_map: dict[str, str]) -> None:
        # value -> model id
        self._by_value_map = by_value_map
        self.get_by_value_calls: list[str] = []

    async def get_by_value(self, value: str):
        self.get_by_value_calls.append(value)
        model_id = self._by_value_map.get(value)
        if not model_id:
            return None
        return SimpleNamespace(id=model_id, value=value, enabled=True)


def _install_service(monkeypatch: pytest.MonkeyPatch, storage: _FakeSettingsStorage) -> None:
    monkeypatch.setattr(config_service, "_settings_service", _FakeSettingsService(storage))


@pytest.mark.asyncio
async def test_migration_backfills_id_when_card_matches_by_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSettingsStorage(legacy_value="claude-3-5-haiku-20241022")
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "SESSION_TITLE_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage({"claude-3-5-haiku-20241022": "card-123"})
    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: fake_model_storage,
    )

    await config_service._migrate_session_title_model_to_id()

    assert storage.set_calls == [("SESSION_TITLE_MODEL_ID", "card-123", "system:migration")]
    assert config_service.settings.SESSION_TITLE_MODEL_ID == "card-123"
    assert config_service._settings_cache["SESSION_TITLE_MODEL_ID"] == "card-123"


@pytest.mark.asyncio
async def test_migration_tries_provider_prefixed_value_when_bare_unmatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSettingsStorage(legacy_value="claude-3-5-haiku-20241022")
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "SESSION_TITLE_MODEL_ID", "")

    # Bare value not stored; only the prefixed form matches.
    fake_model_storage = _FakeModelStorage({"anthropic/claude-3-5-haiku-20241022": "card-456"})
    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: fake_model_storage,
    )

    await config_service._migrate_session_title_model_to_id()

    assert storage.set_calls == [("SESSION_TITLE_MODEL_ID", "card-456", "system:migration")]
    assert "claude-3-5-haiku-20241022" in fake_model_storage.get_by_value_calls
    assert "anthropic/claude-3-5-haiku-20241022" in fake_model_storage.get_by_value_calls


@pytest.mark.asyncio
async def test_migration_warns_when_no_card_matches_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = _FakeSettingsStorage(legacy_value="some-unknown-model")
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "SESSION_TITLE_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage({})
    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: fake_model_storage,
    )

    # Must not raise.
    await config_service._migrate_session_title_model_to_id()

    assert storage.set_calls == []
    assert any("Could not auto-migrate" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_migration_skips_when_legacy_not_explicitly_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # updated_at is None -> legacy value is just the default, never customized.
    storage = _FakeSettingsStorage(legacy_value="claude-3-5-haiku-20241022", legacy_updated=False)
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "SESSION_TITLE_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage({"claude-3-5-haiku-20241022": "card-123"})
    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: fake_model_storage,
    )

    await config_service._migrate_session_title_model_to_id()

    assert storage.set_calls == []
    assert fake_model_storage.get_by_value_calls == []


@pytest.mark.asyncio
async def test_migration_skips_when_new_id_already_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSettingsStorage(
        legacy_value="claude-3-5-haiku-20241022",
        new_id_value="existing-card",
        new_id_updated=True,
    )
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "SESSION_TITLE_MODEL_ID", "existing-card")

    fake_model_storage = _FakeModelStorage({"claude-3-5-haiku-20241022": "card-123"})
    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: fake_model_storage,
    )

    await config_service._migrate_session_title_model_to_id()

    assert storage.set_calls == []
    assert fake_model_storage.get_by_value_calls == []


@pytest.mark.asyncio
async def test_migration_skips_when_legacy_value_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = _FakeSettingsStorage(legacy_value="")
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "SESSION_TITLE_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage({})
    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: fake_model_storage,
    )

    await config_service._migrate_session_title_model_to_id()

    assert storage.set_calls == []
    assert fake_model_storage.get_by_value_calls == []


@pytest.mark.asyncio
async def test_migration_safe_when_no_settings_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_service, "_settings_service", None)

    # Must not raise even though service is absent.
    await config_service._migrate_session_title_model_to_id()
