"""Vision assist: auxiliary vision model description for non-vision main models."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.core import vision_assist


def _image_attachment(**overrides):
    return {
        "id": "img-1",
        "key": "uploads/img.png",
        "name": "img.png",
        "type": "image",
        "mime_type": "image/png",
        "size": 1234,
        "url": "",
        **overrides,
    }


def _doc_attachment(**overrides):
    return {
        "id": "doc-1",
        "key": "uploads/doc.pdf",
        "name": "doc.pdf",
        "type": "document",
        "mime_type": "application/pdf",
        "size": 2048,
        "url": "",
        **overrides,
    }


def _enable_vision_assist(monkeypatch, *, model_id="vision-id", max_bytes=10 * 1024 * 1024):
    """Monkeypatch settings to enable vision assist."""
    fake_settings = SimpleNamespace(
        ENABLE_VISION_ASSIST=True,
        VISION_ASSIST_MODEL_ID=model_id,
        VISION_ASSIST_MAX_BYTES=max_bytes,
    )
    # vision_assist reads settings lazily via `from src.kernel.config import settings`
    import src.kernel.config as config_module

    monkeypatch.setattr(config_module, "settings", fake_settings, raising=True)


def _patch_storage(monkeypatch, *, data_url="data:image/png;base64,aW1hZ2UtYnl0ZXM="):
    storage = MagicMock()
    captured = {}

    async def fake_get_or_init_storage():
        return storage

    # _download_image_as_data_url lives in node_utils and calls storage.download_to_file
    async def fake_download_to_file(key, file, *, chunk_size=1024 * 1024):
        captured["key"] = key
        file.write(b"image-bytes")
        file.seek(0)
        return len(b"image-bytes")

    storage.download_to_file = fake_download_to_file

    monkeypatch.setattr(
        "src.infra.storage.s3.service.get_or_init_storage", fake_get_or_init_storage
    )
    return storage, captured


def _patch_llm(monkeypatch, *, content="A cat sitting on a mat."):
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content=content))

    async def fake_get_model(*, model_id=None):
        return llm

    monkeypatch.setattr("src.infra.llm.client.LLMClient.get_model", fake_get_model)
    return llm


# ─── describe_image_attachments: passthrough ───────────────────────────────


@pytest.mark.asyncio
async def test_passthrough_when_supports_vision_true(monkeypatch):
    """supports_vision=True → no description, attachments unchanged."""
    _enable_vision_assist(monkeypatch)
    _patch_storage(monkeypatch)  # should not be called
    _patch_llm(monkeypatch)  # should not be called

    atts = [_image_attachment()]
    result = await vision_assist.describe_image_attachments(atts, supports_vision=True)

    assert result == atts


@pytest.mark.asyncio
async def test_passthrough_when_disabled(monkeypatch):
    """ENABLE_VISION_ASSIST=False → no description."""
    fake_settings = SimpleNamespace(
        ENABLE_VISION_ASSIST=False,
        VISION_ASSIST_MODEL_ID="vision-id",
        VISION_ASSIST_MAX_BYTES=10 * 1024 * 1024,
    )
    import src.kernel.config as config_module

    monkeypatch.setattr(config_module, "settings", fake_settings)
    _patch_storage(monkeypatch)
    _patch_llm(monkeypatch)

    atts = [_image_attachment()]
    result = await vision_assist.describe_image_attachments(atts, supports_vision=False)

    assert result == atts
    assert "vision_description" not in result[0]


@pytest.mark.asyncio
async def test_passthrough_when_no_model_id(monkeypatch):
    """VISION_ASSIST_MODEL_ID='' → no description."""
    fake_settings = SimpleNamespace(
        ENABLE_VISION_ASSIST=True,
        VISION_ASSIST_MODEL_ID="",
        VISION_ASSIST_MAX_BYTES=10 * 1024 * 1024,
    )
    import src.kernel.config as config_module

    monkeypatch.setattr(config_module, "settings", fake_settings)
    _patch_storage(monkeypatch)
    _patch_llm(monkeypatch)

    atts = [_image_attachment()]
    result = await vision_assist.describe_image_attachments(atts, supports_vision=False)

    assert result == atts
    assert "vision_description" not in result[0]


@pytest.mark.asyncio
async def test_empty_attachments_returns_empty(monkeypatch):
    _enable_vision_assist(monkeypatch)
    result = await vision_assist.describe_image_attachments([], supports_vision=False)
    assert result == []


# ─── describe_image: success ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_image_success(monkeypatch):
    """Enabled + image attachment → vision_description injected."""
    _enable_vision_assist(monkeypatch)
    _patch_storage(monkeypatch)
    llm = _patch_llm(monkeypatch, content="A cat sitting on a mat.")

    atts = [_image_attachment()]
    result = await vision_assist.describe_image_attachments(atts, supports_vision=False)

    assert "vision_description" in result[0]
    assert result[0]["vision_description"] == "A cat sitting on a mat."
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_describe_image_content_list_format(monkeypatch):
    """LLM returns content as a list of blocks — extract text parts."""
    _enable_vision_assist(monkeypatch)
    _patch_storage(monkeypatch)
    _patch_llm(
        monkeypatch,
        content=[{"type": "text", "text": "List desc"}, {"type": "image_url", "image_url": {}}],
    )

    result = await vision_assist.describe_image_attachments(
        [_image_attachment()], supports_vision=False
    )

    assert result[0]["vision_description"] == "List desc"


# ─── describe_image: degradation ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_image_oversize_degrades(monkeypatch):
    """size > max_bytes → skip, no description, no download."""
    _enable_vision_assist(monkeypatch, max_bytes=100)
    storage_captured = _patch_storage(monkeypatch)
    _patch_llm(monkeypatch)

    atts = [_image_attachment(size=200)]
    result = await vision_assist.describe_image_attachments(atts, supports_vision=False)

    assert "vision_description" not in result[0]
    assert "key" not in storage_captured[1]  # storage not touched


@pytest.mark.asyncio
async def test_describe_image_llm_failure_degrades(monkeypatch):
    """LLM raises → None → no description, attachment unchanged."""
    _enable_vision_assist(monkeypatch)
    _patch_storage(monkeypatch)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("model down"))

    async def fake_get_model(*, model_id=None):
        return llm

    monkeypatch.setattr("src.infra.llm.client.LLMClient.get_model", fake_get_model)

    result = await vision_assist.describe_image_attachments(
        [_image_attachment()], supports_vision=False
    )

    assert "vision_description" not in result[0]


@pytest.mark.asyncio
async def test_describe_image_empty_content_degrades(monkeypatch):
    """LLM returns empty content → None → no description."""
    _enable_vision_assist(monkeypatch)
    _patch_storage(monkeypatch)
    _patch_llm(monkeypatch, content="   ")

    result = await vision_assist.describe_image_attachments(
        [_image_attachment()], supports_vision=False
    )

    assert "vision_description" not in result[0]


@pytest.mark.asyncio
async def test_describe_image_download_failure_degrades(monkeypatch):
    """Storage download raises → None → no description."""
    _enable_vision_assist(monkeypatch)

    async def fake_get_or_init_storage():
        raise RuntimeError("storage down")

    monkeypatch.setattr(
        "src.infra.storage.s3.service.get_or_init_storage", fake_get_or_init_storage
    )
    _patch_llm(monkeypatch)

    result = await vision_assist.describe_image_attachments(
        [_image_attachment()], supports_vision=False
    )

    assert "vision_description" not in result[0]


@pytest.mark.asyncio
async def test_describe_image_no_key_degrades(monkeypatch):
    """No key → can't download → None."""
    _enable_vision_assist(monkeypatch)
    _patch_storage(monkeypatch)
    _patch_llm(monkeypatch)

    atts = [_image_attachment(key="")]
    result = await vision_assist.describe_image_attachments(atts, supports_vision=False)

    assert "vision_description" not in result[0]


# ─── non-image passthrough ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_image_attachment_passthrough(monkeypatch):
    """Document attachments are not described."""
    _enable_vision_assist(monkeypatch)
    _patch_storage(monkeypatch)
    _patch_llm(monkeypatch)

    atts = [_doc_attachment()]
    result = await vision_assist.describe_image_attachments(atts, supports_vision=False)

    assert result == atts


@pytest.mark.asyncio
async def test_mixed_image_and_doc_attachments(monkeypatch):
    """Only image attachments get descriptions; docs untouched."""
    _enable_vision_assist(monkeypatch)
    _patch_storage(monkeypatch)
    _patch_llm(monkeypatch, content="image desc")

    atts = [_doc_attachment(), _image_attachment(), _doc_attachment()]
    result = await vision_assist.describe_image_attachments(atts, supports_vision=False)

    assert "vision_description" not in result[0]  # doc
    assert result[1]["vision_description"] == "image desc"
    assert "vision_description" not in result[2]  # doc


# ─── concurrency ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_images_described_concurrently(monkeypatch):
    """Multiple images → all described, order preserved."""
    _enable_vision_assist(monkeypatch)
    _patch_storage(monkeypatch)

    # Each call returns a distinct description based on the image key
    call_count = 0

    async def fake_get_model(*, model_id=None):
        nonlocal call_count

        class FakeLLM:
            async def ainvoke(self, _msg):
                return SimpleNamespace(content=f"desc-{call_count}")

        return FakeLLM()

    monkeypatch.setattr("src.infra.llm.client.LLMClient.get_model", fake_get_model)

    atts = [_image_attachment(key=f"uploads/img{i}.png", name=f"img{i}.png") for i in range(3)]
    result = await vision_assist.describe_image_attachments(atts, supports_vision=False)

    assert len(result) == 3
    # Order preserved (gather preserves order)
    assert [a["name"] for a in result] == ["img0.png", "img1.png", "img2.png"]
    assert all("vision_description" in a for a in result)
