"""Helper functions and constants for WeCom message handling.
"""

import inspect
import mimetypes
from typing import Any
from urllib.parse import unquote, urlparse

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger

logger = get_logger(__name__)

_UPLOAD_FILE_PATH_MARKER = "/api/upload/file/"
WECOM_REVEAL_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
WECOM_REVEAL_DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024
WECOM_REVEAL_LEGACY_DOWNLOAD_MAX_BYTES = 2 * 1024 * 1024


async def _get_backend_object_size(backend: Any, key: str) -> int | None:
    method = getattr(backend, "get_size", None)
    if not callable(method):
        return None
    try:
        size = method(key)
        if inspect.isawaitable(size):
            size = await size
        if isinstance(size, bool) or size is None:
            return None
        value = int(size)
        return value if value >= 0 else None
    except Exception as e:
        logger.debug("[WeCom] Failed to preflight storage object size for %s: %s", key, e)
        return None


def _raise_if_storage_object_too_large(size: int, key: str) -> None:
    if size > WECOM_REVEAL_DOWNLOAD_MAX_BYTES:
        raise ValueError(
            f"Storage object too large for WeCom reveal download: {key} "
            f"size={size} bytes (max {WECOM_REVEAL_DOWNLOAD_MAX_BYTES})"
        )


async def _download_storage_object_to_file(
    backend: Any,
    key: str,
    file: Any,
    *,
    chunk_size: int = WECOM_REVEAL_DOWNLOAD_CHUNK_SIZE,
) -> int:
    """Download storage object into a file sink, preferring streaming APIs."""
    size = await _get_backend_object_size(backend, key)
    if size is not None:
        _raise_if_storage_object_too_large(size, key)

    if hasattr(backend, "download_to_file"):
        return int(await backend.download_to_file(key, file, chunk_size=chunk_size))

    if hasattr(backend, "download_stream"):
        total_size = 0
        async for chunk in backend.download_stream(key, chunk_size=chunk_size):
            if total_size + len(chunk) > WECOM_REVEAL_DOWNLOAD_MAX_BYTES:
                raise ValueError(
                    f"Storage object too large for WeCom reveal download: {key} "
                    f"size>{WECOM_REVEAL_DOWNLOAD_MAX_BYTES} bytes"
                )
            await run_blocking_io(file.write, chunk)
            total_size += len(chunk)
        await run_blocking_io(file.seek, 0)
        return total_size

    data = await backend.download(key)
    if not data:
        return 0
    size = len(data)
    if size > WECOM_REVEAL_LEGACY_DOWNLOAD_MAX_BYTES:
        raise ValueError(
            f"Storage object too large for legacy bytes download: {size} bytes "
            f"(max {WECOM_REVEAL_LEGACY_DOWNLOAD_MAX_BYTES})"
        )
    await run_blocking_io(file.write, data)
    await run_blocking_io(file.seek, 0)
    return size


def _storage_key_from_upload_url(url: str) -> str | None:
    """Extract the 昆小智 storage key from a proxied upload URL."""
    if not url:
        return None
    try:
        path = urlparse(url).path
    except Exception:
        path = url

    if _UPLOAD_FILE_PATH_MARKER not in path:
        return None
    key = path.split(_UPLOAD_FILE_PATH_MARKER, 1)[1]
    return unquote(key).lstrip("/") or None


def _media_name_from_entry(entry: dict[str, Any], key: str | None, url: str, index: int) -> str:
    for field in ("name", "file_name", "filename"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()

    source = key or urlparse(url).path or url
    name = unquote(source.rstrip("/").rsplit("/", 1)[-1])
    return name or f"attachment-{index + 1}.bin"


def _media_mime_type(entry: dict[str, Any], name: str, url: str) -> str:
    for field in ("mime_type", "mimeType", "content_type", "contentType"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return mimetypes.guess_type(name or url)[0] or "application/octet-stream"


def _media_attachment_type(media_type: str, mime_type: str) -> str:
    if media_type == "image" or mime_type.startswith("image/"):
        return "image"
    if media_type == "audio" or mime_type.startswith("audio/"):
        return "audio"
    if media_type == "video" or mime_type.startswith("video/"):
        return "video"
    return "document"


def _media_file_info_from_entry(entry: dict[str, Any], index: int) -> dict[str, Any] | None:
    """Normalize tool media entries into WeComResponseCollector file metadata."""
    media_type = str(entry.get("type") or "").lower()
    if media_type not in {"image", "file", "audio", "video", ""}:
        return None

    url = entry.get("url")
    url = url.strip() if isinstance(url, str) else ""
    key = entry.get("key")
    key = key.strip() if isinstance(key, str) else None
    if not key and url:
        key = _storage_key_from_upload_url(url)
    if not key:
        return None

    name = _media_name_from_entry(entry, key, url, index)
    mime_type = _media_mime_type(entry, name, url)
    return {
        "key": key,
        "name": name,
        "type": _media_attachment_type(media_type, mime_type),
        "mime_type": mime_type,
        "url": url,
    }


def _extract_tool_media_files(result: Any) -> list[dict[str, Any]]:
    """Extract app-storage-backed image/file outputs from tool results."""
    if not isinstance(result, dict):
        return []

    candidates: list[dict[str, Any]] = []

    images = result.get("images")
    if isinstance(images, list):
        candidates.extend(item for item in images if isinstance(item, dict))

    blocks = result.get("blocks")
    if isinstance(blocks, list):
        candidates.extend(
            item
            for item in blocks
            if isinstance(item, dict) and item.get("type") in {"image", "file", "audio", "video"}
        )

    file_infos: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, entry in enumerate(candidates):
        file_info = _media_file_info_from_entry(entry, index)
        if not file_info or file_info["key"] in seen_keys:
            continue
        seen_keys.add(file_info["key"])
        file_infos.append(file_info)
    return file_infos
