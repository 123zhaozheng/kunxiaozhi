"""Server-authoritative attachment lifecycle projection for Agent inputs and history."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from src.infra.logging import get_logger
from src.infra.storage.managed_integration import (
    ManagedStorageError,
    resolve_managed_attachment_statuses,
)

logger = get_logger(__name__)

ATTACHMENT_STATUSES = frozenset(
    {"pending", "active", "delete_pending", "deleted", "forbidden", "missing", "transient"}
)
UNAVAILABLE_ATTACHMENT_STATUSES = frozenset(
    {"pending", "delete_pending", "deleted", "forbidden", "missing", "transient"}
)

_STATUS_MESSAGES = {
    "deleted": "file_deleted",
    "delete_pending": "file_deleted",
    "forbidden": "file_forbidden",
    "missing": "file_missing",
    "transient": "file_status_unavailable",
    "pending": "file_pending",
}


def _get(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _identifier(value: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(value.get("file_id") or value.get("fileId") or "").strip(),
        str(value.get("key") or "").strip(),
    )


def _status_result_map(results: list[Any]) -> dict[tuple[str, str], Any]:
    by_identifier: dict[tuple[str, str], Any] = {}
    for result in results:
        file_id = str(_get(result, "file_id", "fileId", "id", default="") or "").strip()
        key = str(_get(result, "key", "storage_key", default="") or "").strip()
        if file_id:
            by_identifier[(file_id, "")] = result
        if key:
            by_identifier[("", key)] = result
    return by_identifier


def _status_projection(
    attachment: Mapping[str, Any],
    result: Any,
    *,
    authoritative: bool,
) -> dict[str, Any]:
    projected = deepcopy(dict(attachment))
    if not authoritative or result is None:
        # Client lifecycle fields never authorize access.  Old payloads have no
        # lifecycle fields and remain compatible until the server confirms a tombstone.
        projected.pop("status", None)
        projected.pop("lifecycle_error", None)
        projected.pop("reupload_required", None)
        return projected

    status = str(_get(result, "status", "lifecycle", default="missing") or "missing").lower()
    if status not in ATTACHMENT_STATUSES:
        status = "transient"
    projected["status"] = status

    file_id = _get(result, "file_id", "fileId", "id")
    if file_id:
        projected["file_id"] = str(file_id)
    source = _get(result, "source")
    if source:
        projected["source"] = str(source)

    # Name/type/size are tombstone metadata and must survive a logical delete.
    for key in ("name", "type", "mime_type", "mimeType", "size"):
        value = _get(result, key)
        if value is not None:
            projected[key] = value
    if "mimeType" in projected and "mime_type" not in projected:
        projected["mime_type"] = projected["mimeType"]

    server_key = _get(result, "key", "storage_key")
    server_url = _get(result, "url", "content_url", "logical_url")
    if status == "active":
        if server_key and not file_id:
            projected["key"] = str(server_key)
        if file_id:
            projected["key"] = ""
            projected["url"] = str(server_url or f"/api/storage/files/{file_id}/content")
        elif server_url:
            projected["url"] = str(server_url)
        projected.pop("lifecycle_error", None)
        projected.pop("reupload_required", None)
        return projected

    # A non-active file is never passed to a model as a usable object-storage
    # address.  The history card still has its descriptive metadata above.
    projected["key"] = ""
    projected["url"] = ""
    projected["reupload_required"] = True
    projected["lifecycle_error"] = _STATUS_MESSAGES.get(status, "file_unavailable")
    return projected


async def normalize_attachments(
    attachments: list[dict[str, Any]] | None,
    *,
    user_id: str | None,
) -> list[dict[str, Any]] | None:
    """Resolve all attachment lifecycle states in one server-side batch.

    Legacy key-only payloads remain readable when no managed service is installed.
    A modern payload carrying ``file_id`` fails closed if the authoritative lookup
    is unavailable or returns no row.
    """

    if attachments is None:
        return None
    if not attachments:
        return []
    source = [dict(item) for item in attachments if isinstance(item, Mapping)]
    if not source or not user_id:
        return [dict(item) for item in source]

    modern = any(item.get("file_id") or item.get("fileId") for item in source)
    try:
        results = await resolve_managed_attachment_statuses(
            user_id=user_id,
            attachments=source,
        )
    except ManagedStorageError as exc:
        logger.warning("Attachment lifecycle lookup failed for user %s: %s", user_id, exc)
        if modern:
            return [
                _status_projection(
                    item,
                    {"status": "transient"},
                    authoritative=True,
                )
                for item in source
            ]
        return [dict(item) for item in source]

    if results is None:
        if modern:
            return [
                _status_projection(item, {"status": "transient"}, authoritative=True)
                for item in source
            ]
        return [dict(item) for item in source]

    by_identifier = _status_result_map(results)
    projected: list[dict[str, Any]] = []
    for item in source:
        file_id, key = _identifier(item)
        result = by_identifier.get((file_id, "")) or by_identifier.get(("", key))
        if result is None and file_id:
            result = {"status": "missing"}
        projected.append(_status_projection(item, result, authoritative=result is not None))
    return projected


async def normalize_attachment_events(
    events: list[dict[str, Any]],
    *,
    user_id: str | None,
) -> list[dict[str, Any]]:
    """Project user-message attachment states without mutating stored events."""

    if not events:
        return []
    locations: list[tuple[int, int]] = []
    attachments: list[dict[str, Any]] = []
    projected_events = deepcopy(events)
    for event_index, event in enumerate(projected_events):
        data = event.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("attachments"), list):
            continue
        for attachment_index, attachment in enumerate(data["attachments"]):
            if isinstance(attachment, Mapping):
                locations.append((event_index, attachment_index))
                attachments.append(dict(attachment))
    if not attachments:
        return projected_events
    projected = await normalize_attachments(attachments, user_id=user_id) or []
    for (event_index, attachment_index), attachment in zip(locations, projected, strict=False):
        projected_events[event_index]["data"]["attachments"][attachment_index] = attachment
    return projected_events


def attachment_file_id(attachment: Mapping[str, Any]) -> str | None:
    value = attachment.get("file_id") or attachment.get("fileId")
    clean = str(value).strip() if value else ""
    return clean or None


def attachment_is_unavailable(attachment: Mapping[str, Any]) -> bool:
    status = str(attachment.get("status") or "").lower()
    return status in UNAVAILABLE_ATTACHMENT_STATUSES


def attachment_reupload_context(attachment: Mapping[str, Any]) -> dict[str, str] | None:
    status = str(attachment.get("status") or "").lower()
    if status not in UNAVAILABLE_ATTACHMENT_STATUSES:
        return None
    return {
        "code": _STATUS_MESSAGES.get(status, "file_unavailable"),
        "filename": str(attachment.get("name") or "unknown file"),
        "action": "reupload_required",
    }


__all__ = [
    "ATTACHMENT_STATUSES",
    "UNAVAILABLE_ATTACHMENT_STATUSES",
    "attachment_file_id",
    "attachment_is_unavailable",
    "attachment_reupload_context",
    "normalize_attachment_events",
    "normalize_attachments",
]
