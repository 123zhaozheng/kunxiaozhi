"""Tests for stage-2 one-time migrations of legacy bare-string triplets to *_MODEL_ID."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.kernel.config import service as config_service


class _FakeSettingItem(SimpleNamespace):
    pass


class _FakeSettingsStorage:
    """Single-legacy + single-new-id storage used by the migration helper."""

    def __init__(
        self,
        *,
        legacy_key: str,
        new_id_key: str,
        legacy_value: object = "some-model",
        legacy_updated: bool = True,
        new_id_value: str = "",
        new_id_updated: bool = False,
    ) -> None:
        self._legacy_key = legacy_key
        self._new_id_key = new_id_key
        self._legacy_value = legacy_value
        self._legacy_updated = legacy_updated
        self._new_id_value = new_id_value
        self._new_id_updated = new_id_updated
        self.set_calls: list[tuple[str, object, str]] = []

    async def get_raw(self, key: str):
        if key == self._legacy_key:
            if self._legacy_value is None and not self._legacy_updated:
                return None
            return _FakeSettingItem(
                key=key,
                value=self._legacy_value,
                updated_at="2026-01-01T00:00:00" if self._legacy_updated else None,
            )
        if key == self._new_id_key:
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
    """value -> (model_id, kind) map; returns a SimpleNamespace card."""

    def __init__(self, by_value_map: dict[str, tuple[str, str]]) -> None:
        # value -> (model_id, kind)
        self._by_value_map = by_value_map
        self.get_by_value_calls: list[str] = []

    async def get_by_value(self, value: str):
        self.get_by_value_calls.append(value)
        entry = self._by_value_map.get(value)
        if not entry:
            return None
        model_id, kind = entry
        return SimpleNamespace(id=model_id, value=value, enabled=True, kind=kind)


def _install_service(monkeypatch: pytest.MonkeyPatch, storage: _FakeSettingsStorage) -> None:
    monkeypatch.setattr(config_service, "_settings_service", _FakeSettingsService(storage))


def _install_model_storage(
    monkeypatch: pytest.MonkeyPatch, fake_model_storage: _FakeModelStorage
) -> None:
    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: fake_model_storage,
    )


# ---------------------------------------------------------------------------
# NATIVE_MEMORY_MODEL -> NATIVE_MEMORY_MODEL_ID (kind=chat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_memory_migration_backfills_id_for_chat_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSettingsStorage(
        legacy_key="NATIVE_MEMORY_MODEL",
        new_id_key="NATIVE_MEMORY_MODEL_ID",
        legacy_value="claude-3-5-haiku-20241022",
    )
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "NATIVE_MEMORY_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage(
        {"claude-3-5-haiku-20241022": ("card-chat", "chat")}
    )
    _install_model_storage(monkeypatch, fake_model_storage)

    await config_service._migrate_native_memory_model_to_id()

    assert storage.set_calls == [("NATIVE_MEMORY_MODEL_ID", "card-chat", "system:migration")]
    assert config_service.settings.NATIVE_MEMORY_MODEL_ID == "card-chat"
    assert config_service._settings_cache["NATIVE_MEMORY_MODEL_ID"] == "card-chat"


@pytest.mark.asyncio
async def test_native_memory_migration_skips_legacy_chat_card_without_kind_as_non_chat(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A legacy card without `kind` (defaults to chat) must NOT match a
    # non-chat migration; embedding migration should warn instead.
    storage = _FakeSettingsStorage(
        legacy_key="NATIVE_MEMORY_EMBEDDING_MODEL",
        new_id_key="NATIVE_MEMORY_EMBEDDING_MODEL_ID",
        legacy_value="text-embedding-3-small",
    )
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "NATIVE_MEMORY_EMBEDDING_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage(
        {"text-embedding-3-small": ("card-legacy", None)}
    )
    _install_model_storage(monkeypatch, fake_model_storage)

    await config_service._migrate_native_memory_embedding_model_to_id()

    assert storage.set_calls == []
    assert any("Could not auto-migrate" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# AUDIO_TRANSCRIPTION_MODEL -> AUDIO_TRANSCRIPTION_MODEL_ID (kind=transcribe)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audio_transcription_migration_backfills_id_for_transcribe_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSettingsStorage(
        legacy_key="AUDIO_TRANSCRIPTION_MODEL",
        new_id_key="AUDIO_TRANSCRIPTION_MODEL_ID",
        legacy_value="gpt-4o-mini-transcribe",
    )
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "AUDIO_TRANSCRIPTION_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage(
        {"gpt-4o-mini-transcribe": ("card-transcribe", "transcribe")}
    )
    _install_model_storage(monkeypatch, fake_model_storage)

    await config_service._migrate_audio_transcription_model_to_id()

    assert storage.set_calls == [
        ("AUDIO_TRANSCRIPTION_MODEL_ID", "card-transcribe", "system:migration")
    ]
    assert config_service.settings.AUDIO_TRANSCRIPTION_MODEL_ID == "card-transcribe"


@pytest.mark.asyncio
async def test_audio_transcription_migration_warns_when_no_transcribe_card(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = _FakeSettingsStorage(
        legacy_key="AUDIO_TRANSCRIPTION_MODEL",
        new_id_key="AUDIO_TRANSCRIPTION_MODEL_ID",
        legacy_value="gpt-4o-mini-transcribe",
    )
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "AUDIO_TRANSCRIPTION_MODEL_ID", "")

    # A chat-kind card with the same value must not match a transcribe migration.
    fake_model_storage = _FakeModelStorage(
        {"gpt-4o-mini-transcribe": ("card-chat", "chat")}
    )
    _install_model_storage(monkeypatch, fake_model_storage)

    await config_service._migrate_audio_transcription_model_to_id()

    assert storage.set_calls == []
    assert any("Could not auto-migrate" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# NATIVE_MEMORY_EMBEDDING_MODEL -> NATIVE_MEMORY_EMBEDDING_MODEL_ID (kind=embedding)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_migration_backfills_id_for_embedding_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSettingsStorage(
        legacy_key="NATIVE_MEMORY_EMBEDDING_MODEL",
        new_id_key="NATIVE_MEMORY_EMBEDDING_MODEL_ID",
        legacy_value="text-embedding-3-small",
    )
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "NATIVE_MEMORY_EMBEDDING_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage(
        {"text-embedding-3-small": ("card-emb", "embedding")}
    )
    _install_model_storage(monkeypatch, fake_model_storage)

    await config_service._migrate_native_memory_embedding_model_to_id()

    assert storage.set_calls == [
        ("NATIVE_MEMORY_EMBEDDING_MODEL_ID", "card-emb", "system:migration")
    ]
    assert config_service.settings.NATIVE_MEMORY_EMBEDDING_MODEL_ID == "card-emb"


# ---------------------------------------------------------------------------
# NATIVE_MEMORY_RERANK_MODEL -> NATIVE_MEMORY_RERANK_MODEL_ID (kind=rerank)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_migration_backfills_id_for_rerank_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSettingsStorage(
        legacy_key="NATIVE_MEMORY_RERANK_MODEL",
        new_id_key="NATIVE_MEMORY_RERANK_MODEL_ID",
        legacy_value="bge-reranker-v2-m3",
    )
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "NATIVE_MEMORY_RERANK_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage(
        {"bge-reranker-v2-m3": ("card-rerank", "rerank")}
    )
    _install_model_storage(monkeypatch, fake_model_storage)

    await config_service._migrate_native_memory_rerank_model_to_id()

    assert storage.set_calls == [
        ("NATIVE_MEMORY_RERANK_MODEL_ID", "card-rerank", "system:migration")
    ]
    assert config_service.settings.NATIVE_MEMORY_RERANK_MODEL_ID == "card-rerank"


# ---------------------------------------------------------------------------
# Shared skip conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_skips_when_new_id_already_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSettingsStorage(
        legacy_key="NATIVE_MEMORY_RERANK_MODEL",
        new_id_key="NATIVE_MEMORY_RERANK_MODEL_ID",
        legacy_value="bge-reranker-v2-m3",
        new_id_value="existing-card",
        new_id_updated=True,
    )
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "NATIVE_MEMORY_RERANK_MODEL_ID", "existing-card")

    fake_model_storage = _FakeModelStorage(
        {"bge-reranker-v2-m3": ("card-rerank", "rerank")}
    )
    _install_model_storage(monkeypatch, fake_model_storage)

    await config_service._migrate_native_memory_rerank_model_to_id()

    assert storage.set_calls == []
    assert fake_model_storage.get_by_value_calls == []


@pytest.mark.asyncio
async def test_migration_skips_when_legacy_not_explicitly_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeSettingsStorage(
        legacy_key="NATIVE_MEMORY_MODEL",
        new_id_key="NATIVE_MEMORY_MODEL_ID",
        legacy_value="claude-3-5-haiku-20241022",
        legacy_updated=False,
    )
    _install_service(monkeypatch, storage)
    monkeypatch.setattr(config_service.settings, "NATIVE_MEMORY_MODEL_ID", "")

    fake_model_storage = _FakeModelStorage(
        {"claude-3-5-haiku-20241022": ("card-chat", "chat")}
    )
    _install_model_storage(monkeypatch, fake_model_storage)

    await config_service._migrate_native_memory_model_to_id()

    assert storage.set_calls == []
    assert fake_model_storage.get_by_value_calls == []


@pytest.mark.asyncio
async def test_migration_safe_when_no_settings_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_service, "_settings_service", None)

    # Must not raise even though service is absent.
    await config_service._migrate_native_memory_model_to_id()
    await config_service._migrate_audio_transcription_model_to_id()
    await config_service._migrate_native_memory_embedding_model_to_id()
    await config_service._migrate_native_memory_rerank_model_to_id()
