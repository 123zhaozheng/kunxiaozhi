"""Tests for Dify KB dataset id resolution into agent_options."""

from __future__ import annotations

from src.infra.persona_preset.dify_kb_agent_options import (
    apply_dify_kb_dataset_ids_to_agent_options,
    resolve_dify_kb_dataset_ids,
)
from src.kernel.config import settings
from src.kernel.schemas.persona_preset import PersonaPresetSnapshot


def _snapshot(*, dataset_ids: list[str]) -> PersonaPresetSnapshot:
    return PersonaPresetSnapshot(
        preset_id="p1",
        name="Test",
        system_prompt="sys",
        dify_kb_dataset_ids=dataset_ids,
    )


def test_resolve_prefers_persona_ids() -> None:
    snap = _snapshot(dataset_ids=["kb-a", "kb-b"])
    assert resolve_dify_kb_dataset_ids(snap, default_dataset_ids=["default"]) == [
        "kb-a",
        "kb-b",
    ]


def test_resolve_falls_back_to_default_when_persona_empty() -> None:
    snap = _snapshot(dataset_ids=[])
    assert resolve_dify_kb_dataset_ids(snap, default_dataset_ids=["d1"]) == ["d1"]
    assert resolve_dify_kb_dataset_ids(None, default_dataset_ids=["d2"]) == ["d2"]


def test_resolve_empty_when_no_persona_and_no_default() -> None:
    assert resolve_dify_kb_dataset_ids(None, default_dataset_ids=[]) == []


def test_apply_sets_key_only_when_non_empty() -> None:
    opts: dict = {}
    apply_dify_kb_dataset_ids_to_agent_options(
        opts,
        persona_snapshot=_snapshot(dataset_ids=["x"]),
    )
    assert opts == {"dify_kb_dataset_ids": ["x"]}

    opts2: dict = {}
    apply_dify_kb_dataset_ids_to_agent_options(
        opts2,
        persona_snapshot=_snapshot(dataset_ids=[]),
        default_dataset_ids=[],
    )
    assert "dify_kb_dataset_ids" not in opts2


def test_apply_uses_settings_default_when_not_overridden(
    monkeypatch,
) -> None:
    settings.DIFY_KB_DEFAULT_DATASET_IDS = ["sys-default"]
    opts: dict = {}
    apply_dify_kb_dataset_ids_to_agent_options(opts, persona_snapshot=None)
    assert opts == {"dify_kb_dataset_ids": ["sys-default"]}
    settings.DIFY_KB_DEFAULT_DATASET_IDS = []