"""Resolve Dify KB dataset ids for agent runtime (Web + WeCom)."""

from __future__ import annotations

from typing import Any

from src.kernel.config import settings
from src.kernel.schemas.persona_preset import PersonaPresetSnapshot


def resolve_dify_kb_dataset_ids(
    persona_snapshot: PersonaPresetSnapshot | None,
    *,
    default_dataset_ids: list[str] | None = None,
) -> list[str]:
    """Persona-bound ids first; otherwise system default list. May be empty."""
    persona_ids = (
        list(persona_snapshot.dify_kb_dataset_ids)
        if persona_snapshot and persona_snapshot.dify_kb_dataset_ids
        else []
    )
    if persona_ids:
        return persona_ids
    fallback = (
        default_dataset_ids
        if default_dataset_ids is not None
        else list(settings.DIFY_KB_DEFAULT_DATASET_IDS or [])
    )
    return list(fallback)


def apply_dify_kb_dataset_ids_to_agent_options(
    agent_options: dict[str, Any] | None,
    *,
    persona_snapshot: PersonaPresetSnapshot | None = None,
    default_dataset_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Mutate or create agent_options; set dify_kb_dataset_ids only when non-empty."""
    opts = agent_options if agent_options is not None else {}
    resolved_ids = resolve_dify_kb_dataset_ids(
        persona_snapshot,
        default_dataset_ids=default_dataset_ids,
    )
    if resolved_ids:
        opts["dify_kb_dataset_ids"] = resolved_ids
    return opts
