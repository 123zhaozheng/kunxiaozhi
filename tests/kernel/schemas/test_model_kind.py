"""Tests for the model card `kind` field and DB backward compatibility."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.kernel.schemas.model import (
    AvailableModel,
    ModelConfig,
    ModelConfigCreate,
    ModelConfigUpdate,
    to_available_model,
)


def test_model_config_kind_defaults_to_chat() -> None:
    model = ModelConfig(value="anthropic/claude-3-5-sonnet", label="Claude")

    assert model.kind == "chat"


def test_model_config_kind_accepts_non_chat_values() -> None:
    for kind in ("embedding", "rerank", "transcribe"):
        model = ModelConfig(value="text-embedding-3-small", label="emb", kind=kind)

        assert model.kind == kind


def test_model_config_create_kind_defaults_to_chat() -> None:
    create = ModelConfigCreate(value="whisper-1", label="Whisper")

    assert create.kind == "chat"


def test_model_config_update_kind_is_optional() -> None:
    update = ModelConfigUpdate()

    assert update.kind is None


def test_legacy_db_doc_without_kind_defaults_to_chat() -> None:
    # Older cards in MongoDB do not carry a `kind` field. Loading them must
    # not fail and must treat the card as a chat model.
    legacy_doc = {
        "id": "card-1",
        "value": "anthropic/claude-3-5-sonnet",
        "label": "Claude",
        "enabled": True,
        "order": 0,
    }
    model = ModelConfig(**legacy_doc)

    assert model.kind == "chat"


def test_to_available_model_propagates_kind() -> None:
    model = ModelConfig(value="bge-reranker-v2-m3", label="BGE Reranker", kind="rerank")

    available = to_available_model(model)

    assert isinstance(available, AvailableModel)
    assert available.kind == "rerank"


def test_to_available_model_defaults_kind_to_chat_for_legacy_card() -> None:
    model = ModelConfig(value="gpt-4o-mini", label="GPT")

    available = to_available_model(model)

    assert available.kind == "chat"


def test_model_config_kind_rejects_unknown_value() -> None:
    """The kind field is constrained to chat/embedding/rerank/transcribe so the
    value domain stays consistent across backend, migrations, and the frontend
    kind filter."""
    with pytest.raises(ValidationError):
        ModelConfig(value="m", label="m", kind="reasoning")  # type: ignore[arg-type]


def test_model_config_create_kind_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ModelConfigCreate(value="m", label="m", kind="reasoning")  # type: ignore[arg-type]
