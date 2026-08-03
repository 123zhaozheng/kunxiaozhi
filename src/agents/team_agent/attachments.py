"""TeamAgent attachment contracts and deterministic sandbox materialization."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.infra.logging import get_logger
from src.infra.storage.s3.service import get_or_init_storage
from src.infra.upload.file_record import FileRecordStorage

logger = get_logger(__name__)

AttachmentStatus = Literal["pending", "materialized", "failed"]


class AttachmentMaterializationError(BaseModel):
    """Actionable failure associated with one attachment."""

    code: str = "materialization_failed"
    stage: Literal["validation", "ownership", "download", "upload", "verification"]
    reason: str


class AttachmentManifestItem(BaseModel):
    """Immutable-enough attachment state passed to planning and handoffs."""

    model_config = ConfigDict(populate_by_name=True)

    attachment_id: str = Field(alias="id")
    key: str
    name: str
    mime_type: str = Field(default="", alias="mimeType")
    size: int = Field(default=0, ge=0)
    sandbox_path: str | None = None
    status: AttachmentStatus = "pending"
    error: AttachmentMaterializationError | None = None

    @property
    def id(self) -> str:
        return self.attachment_id


class AttachmentManifest(BaseModel):
    """Materialization result keyed by attachment id."""

    model_config = ConfigDict(populate_by_name=True)

    attachments: list[AttachmentManifestItem] = Field(default_factory=list)
    work_dir: str

    @property
    def by_id(self) -> dict[str, AttachmentManifestItem]:
        return {item.attachment_id: item for item in self.attachments}

    @property
    def successful(self) -> bool:
        return all(item.status == "materialized" for item in self.attachments)


def _value(attachment: Any, name: str, default: Any = None) -> Any:
    if isinstance(attachment, Mapping):
        return attachment.get(name, default)
    return getattr(attachment, name, default)


def _safe_component(value: str, *, fallback: str) -> str:
    value = str(value or "").strip()
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return value[:160] or fallback


def _destination(work_dir: str, attachment_id: str, name: str) -> str:
    """Build a POSIX sandbox path without trusting user-controlled components."""
    root = str(work_dir or "").rstrip("/")
    if not root.startswith("/"):
        raise ValueError("sandbox work_dir must be an absolute provider path")
    safe_id = _safe_component(attachment_id.replace("/", "-"), fallback="attachment")
    safe_name = _safe_component(name, fallback="file")
    path = posixpath.normpath(posixpath.join(root, "attachments", safe_id, safe_name))
    expected_prefix = posixpath.join(root, "attachments", safe_id) + "/"
    if not path.startswith(expected_prefix):
        raise ValueError("attachment path escapes the sandbox work directory")
    return path


def _concrete_backend(backend: Any) -> Any:
    """Unwrap CompositeBackend so uploads target the provider filesystem."""
    return getattr(backend, "default", backend)


async def _existing_size(backend: Any, path: str) -> int | None:
    """Return an existing file size when the backend exposes metadata."""
    parent = posixpath.dirname(path)
    try:
        listing = await backend.als(parent) if hasattr(backend, "als") else None
        if listing is None or getattr(listing, "error", None):
            return None
        for entry in getattr(listing, "entries", None) or []:
            entry_path = entry.get("path") if isinstance(entry, Mapping) else getattr(entry, "path", None)
            if entry_path == path:
                value = entry.get("size") if isinstance(entry, Mapping) else getattr(entry, "size", None)
                return int(value) if value is not None else None
    except Exception:
        # Metadata support varies by provider; upload response validation below
        # remains mandatory even when verification is unavailable.
        return None
    return None


class AttachmentMaterializer:
    """Download storage objects and upload them into a shared sandbox."""

    def __init__(
        self,
        *,
        storage: Any | None = None,
        file_records: Any | None = None,
        require_ownership: bool = True,
    ) -> None:
        self.storage = storage
        self.file_records = file_records
        self.require_ownership = require_ownership

    async def materialize(
        self,
        attachments: Sequence[Any] | None,
        *,
        backend: Any,
        work_dir: str,
        user_id: str | None = None,
    ) -> AttachmentManifest:
        concrete = _concrete_backend(backend)
        manifest: list[AttachmentManifestItem] = []
        pending_uploads: list[tuple[str, bytes, AttachmentManifestItem]] = []

        for raw in attachments or []:
            item = self._build_item(raw)
            try:
                item.sandbox_path = _destination(work_dir, item.attachment_id, item.name)
                await self._validate_record(item, user_id)
                existing = await _existing_size(concrete, item.sandbox_path)
                if existing is not None and existing == item.size:
                    item.status = "materialized"
                else:
                    try:
                        storage = self.storage or await get_or_init_storage()
                        payload = await storage.download_file(item.key)
                        if item.size and len(payload) != item.size:
                            raise _MaterializationFailureError(
                                "download", f"size mismatch: expected {item.size}, got {len(payload)}"
                            )
                    except _MaterializationFailureError:
                        raise
                    except Exception as exc:
                        raise _MaterializationFailureError("download", str(exc)) from exc
                    pending_uploads.append((item.sandbox_path, payload, item))
            except _MaterializationFailureError as exc:
                item.status = "failed"
                item.error = AttachmentMaterializationError(stage=exc.stage, reason=exc.reason)
            except Exception as exc:
                item.status = "failed"
                item.error = AttachmentMaterializationError(stage="validation", reason=str(exc))
            manifest.append(item)

        if pending_uploads:
            try:
                if hasattr(concrete, "aupload_files"):
                    responses = await concrete.aupload_files(
                        [(path, payload) for path, payload, _item in pending_uploads]
                    )
                else:
                    from src.infra.async_utils import run_blocking_io

                    responses = await run_blocking_io(
                        concrete.upload_files,
                        [(path, payload) for path, payload, _item in pending_uploads],
                    )
                if len(responses) != len(pending_uploads):
                    raise _MaterializationFailureError(
                        "upload",
                        f"backend returned {len(responses)} responses for {len(pending_uploads)} uploads",
                    )
                for response, (path, _payload, item) in zip(responses, pending_uploads, strict=True):
                    if isinstance(response, Mapping):
                        error = response.get("error")
                        response_path = response.get("path", path)
                    else:
                        error = getattr(response, "error", None)
                        response_path = getattr(response, "path", path)
                    if response_path != path:
                        item.status = "failed"
                        item.error = AttachmentMaterializationError(
                            stage="verification",
                            reason=f"backend returned path {response_path!r}, expected {path!r}",
                        )
                        continue
                    if error:
                        item.status = "failed"
                        item.error = AttachmentMaterializationError(
                            stage="upload", reason=f"{response_path}: {error}"
                        )
                    else:
                        item.status = "materialized"
            except _MaterializationFailureError as exc:
                for _path, _payload, item in pending_uploads:
                    item.status = "failed"
                    item.error = AttachmentMaterializationError(stage=exc.stage, reason=exc.reason)
            except Exception as exc:
                for _path, _payload, item in pending_uploads:
                    item.status = "failed"
                    item.error = AttachmentMaterializationError(stage="upload", reason=str(exc))

        return AttachmentManifest(attachments=manifest, work_dir=work_dir)

    async def _validate_record(self, item: AttachmentManifestItem, user_id: str | None) -> None:
        if not item.key:
            raise _MaterializationFailureError("validation", "storage key is required")
        if not user_id or not self.require_ownership:
            return
        records = self.file_records or FileRecordStorage()
        record = await records.find_by_key(item.key)
        if not record:
            raise _MaterializationFailureError("ownership", "attachment storage record was not found")
        owner = record.get("uploaded_by") or record.get("user_id")
        if owner and str(owner) != str(user_id):
            raise _MaterializationFailureError("ownership", "attachment is not owned by the current user")

    @staticmethod
    def _build_item(raw: Any) -> AttachmentManifestItem:
        attachment_id = _value(raw, "attachment_id") or _value(raw, "id") or _value(raw, "key")
        mime_type = _value(raw, "mime_type") or _value(raw, "mimeType") or _value(raw, "type") or ""
        return AttachmentManifestItem(
            attachment_id=str(attachment_id or "attachment"),
            key=str(_value(raw, "key") or ""),
            name=str(_value(raw, "name") or "file"),
            mimeType=str(mime_type),
            size=int(_value(raw, "size") or 0),
        )


class _MaterializationFailureError(Exception):
    def __init__(self, stage: Literal["validation", "ownership", "download", "upload", "verification"], reason: str):
        super().__init__(reason)
        self.stage = stage
        self.reason = reason


async def materialize_attachments(
    attachments: Sequence[Any] | None,
    *,
    backend: Any,
    work_dir: str,
    user_id: str | None = None,
    storage: Any | None = None,
    file_records: Any | None = None,
) -> AttachmentManifest:
    """Functional entry point used by TeamAgent preflight and recovery."""
    return await AttachmentMaterializer(storage=storage, file_records=file_records).materialize(
        attachments,
        backend=backend,
        work_dir=work_dir,
        user_id=user_id,
    )


__all__ = [
    "AttachmentManifest",
    "AttachmentManifestItem",
    "AttachmentMaterializationError",
    "AttachmentMaterializer",
    "materialize_attachments",
]
