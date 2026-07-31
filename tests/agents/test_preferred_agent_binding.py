"""Tests for preferred_agent_id schema defaults and resolve_persona_agent_id."""

from __future__ import annotations

from src.agents.core.persona import resolve_persona_agent_id
from src.kernel.schemas.persona_preset import (
    DEFAULT_PREFERRED_AGENT_ID,
    PersonaPreset,
    PersonaPresetCreate,
    PersonaPresetScope,
    PersonaPresetSnapshot,
    PersonaPresetStatus,
    PersonaPresetUpdate,
    PersonaPresetVisibility,
)


def test_create_without_preferred_defaults_to_fast() -> None:
    payload = PersonaPresetCreate(
        name="Writer",
        system_prompt="You are a writer.",
    )
    assert payload.preferred_agent_id == "fast"
    assert payload.preferred_agent_id == DEFAULT_PREFERRED_AGENT_ID


def test_update_accepts_preferred_search() -> None:
    payload = PersonaPresetUpdate(preferred_agent_id="search")
    assert payload.preferred_agent_id == "search"


def test_read_missing_preferred_parses_as_fast_without_required_field() -> None:
    # Simulate legacy Mongo/JSON docs without preferred_agent_id.
    preset = PersonaPreset.model_validate(
        {
            "id": "pp_legacy",
            "name": "Legacy",
            "system_prompt": "hello",
            "visibility": PersonaPresetVisibility.PRIVATE.value,
            "status": PersonaPresetStatus.PUBLISHED.value,
            "scope": PersonaPresetScope.USER.value,
        }
    )
    assert preset.preferred_agent_id == "fast"


def test_snapshot_defaults_preferred_to_fast() -> None:
    snapshot = PersonaPresetSnapshot(
        preset_id="pp1",
        name="N",
        system_prompt="S",
    )
    assert snapshot.preferred_agent_id == "fast"


def test_resolve_persona_agent_id_prefers_preferred() -> None:
    assert resolve_persona_agent_id("fast", "search") == "search"
    assert resolve_persona_agent_id("search", "team") == "search"
    assert resolve_persona_agent_id(None, "fast") == "fast"


def test_resolve_persona_agent_id_falls_back_to_requested_then_fast() -> None:
    assert resolve_persona_agent_id("search", None) == "search"
    assert resolve_persona_agent_id("search", "invalid") == "search"
    assert resolve_persona_agent_id(None, None) == "fast"
    assert resolve_persona_agent_id("nope", "nope") == "fast"
