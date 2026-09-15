"""Small integration seam for user-owned managed storage lifecycle operations.

The storage foundation owns Mongo ledgers, ownership rows, and purge workers.  Agent,
Skill, and channel code only needs this narrow seam so those domains do not depend on
collection names or the foundation's implementation details.
"""

from __future__ import annotations

import importlib
import inspect
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.infra.logging import get_logger

logger = get_logger(__name__)


class ManagedStorageError(RuntimeError):
    """Raised when a managed lifecycle operation cannot be completed safely."""


class ManagedStorageUnavailableError(ManagedStorageError):
    """Raised when an active integration cannot reach the lifecycle service."""


@dataclass(frozen=True)
class ManagedFilePlan:
    """The subset of a foundation reservation needed by an adapter."""

    file_id: str | None = None
    storage_key: str | None = None
    status: str = "active"
    generation: str | None = None
    raw: Any = None


_SERVICE: Any | None = None
_DISCOVERY_MODULES = (
    "src.infra.storage.managed_service",
    "src.infra.storage.user_storage",
    "src.infra.storage.lifecycle",
)
_FACTORY_NAMES = (
    "get_managed_storage_service",
    "get_user_storage_service",
    "get_storage_lifecycle_service",
)
_SERVICE_CLASS_NAMES = ("UserStorageQuotaService", "StorageDomainService", "QuotaService")


def set_managed_storage_service(service: Any | None) -> None:
    """Install the foundation service for tests or application startup."""

    global _SERVICE
    _SERVICE = service


def has_explicit_managed_storage_service() -> bool:
    """Whether startup/tests explicitly installed the lifecycle service."""

    return _SERVICE is not None


def get_managed_storage_service() -> Any | None:
    """Return the installed foundation service, if one is available."""

    if _SERVICE is not None:
        return _SERVICE

    for module_name in _DISCOVERY_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            # The foundation may be installed during a rolling deployment.  A
            # partially imported module must not make legacy adapters crash;
            # once its startup import succeeds discovery will pick it up.
            logger.debug("Managed storage discovery skipped %s: %s", module_name, exc)
            continue
        for factory_name in _FACTORY_NAMES:
            factory = getattr(module, factory_name, None)
            if not callable(factory):
                continue
            try:
                service = factory()
            except TypeError:
                # Some foundations expose a singleton directly rather than a
                # zero-argument factory.  The next candidate may still work.
                continue
            if service is not None:
                return service
        for class_name in _SERVICE_CLASS_NAMES:
            service_class = getattr(module, class_name, None)
            if service_class is None or not callable(service_class):
                continue
            try:
                return service_class()
            except Exception as exc:
                logger.debug("Managed storage class %s is not ready: %s", class_name, exc)
    return None


def _value(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def managed_file_plan(value: Any) -> ManagedFilePlan:
    """Extract a stable plan from a dict or Pydantic result."""

    if isinstance(value, ManagedFilePlan):
        return value
    return ManagedFilePlan(
        file_id=_value(value, "file_id", "id"),
        storage_key=_value(value, "storage_key", "key"),
        status=str(_value(value, "status", "lifecycle", default="active") or "active"),
        generation=_value(value, "generation", "write_generation"),
        raw=value,
    )


def managed_file_plan_for(value: Any, source_ref: str | None = None) -> ManagedFilePlan:
    """Resolve one item from an aggregate reservation when available."""

    if isinstance(value, Mapping) and isinstance(value.get("items"), list):
        for item in value["items"]:
            if source_ref is None or item.get("source_ref") == source_ref:
                return managed_file_plan(item.get("prepared", item))
    return managed_file_plan(value)


async def _invoke(service: Any, names: Sequence[str], **kwargs: Any) -> Any:
    """Invoke the first supported service method without fixing its internals."""

    for name in names:
        method = getattr(service, name, None)
        if not callable(method):
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            signature = None
        call_kwargs = kwargs
        if signature is not None and not any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ):
            call_kwargs = {
                key: value
                for key, value in kwargs.items()
                if key in signature.parameters
            }
        result = method(**call_kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    raise ManagedStorageUnavailableError(
        f"managed storage service does not implement any of: {', '.join(names)}"
    )


def _service_or_none() -> Any | None:
    return get_managed_storage_service()


async def reserve_managed_files(
    *,
    user_id: str,
    source: str,
    items: list[dict[str, Any]],
    idempotency_key: str,
    operation_kind: str = "group_create",
) -> Any | None:
    """Reserve a bounded set of user-owned bytes before physical writes.

    ``None`` means the storage foundation is not installed in this process.  Once a
    service is installed, missing/failed reservation is deliberately an exception so
    an adapter cannot silently fall back to an untracked write.
    """

    service = _service_or_none()
    if service is None:
        return None
    prepare_group_create = getattr(service, "prepare_group_create", None)
    if callable(prepare_group_create):
        try:
            prepared = await prepare_group_create(
                user_id,
                source=source,
                items=items,
                idempotency_key=idempotency_key,
            )
            prepared_items = [
                {**item, "prepared": prepared_item, "storage_key": managed_file_plan(prepared_item).storage_key}
                for item, prepared_item in zip(items, prepared, strict=True)
            ]
        except Exception as exc:
            raise ManagedStorageError("managed storage group reservation failed") from exc
    else:
        prepare_create = getattr(service, "prepare_create", None)
        if not callable(prepare_create):
            try:
                return await _invoke(
                    service,
                    ("reserve_group_create", "reserve_files", "reserve_create", "reserve"),
                    user_id=user_id,
                    source=source,
                    items=items,
                    idempotency_key=idempotency_key,
                    operation_kind=operation_kind,
                )
            except ManagedStorageUnavailableError:
                raise
            except Exception as exc:
                raise ManagedStorageError("managed storage reservation failed") from exc
        prepared_items = []
        try:
            # Compatibility fallback for an older lifecycle service. New storage
            # foundations always take the single group path above.
            for index, item in enumerate(items):
                storage_key = str(
                    item.get("storage_key")
                    or f"managed/{source}/{user_id}/{uuid.uuid4().hex}"
                )
                prepared = await prepare_create(
                    user_id,
                    source=source,
                    name=str(item.get("name") or "attachment"),
                    mime_type=str(item.get("mime_type") or "application/octet-stream"),
                    category=str(item.get("category") or source),
                    size=int(item.get("size") or 0),
                    content_hash=str(item.get("content_hash") or ""),
                    storage_key=storage_key,
                    idempotency_key=f"{idempotency_key}:{index}",
                    source_ref=item.get("source_ref"),
                )
                prepared_items.append({**item, "prepared": prepared, "storage_key": storage_key})
        except Exception as exc:
            for prepared_item in prepared_items:
                try:
                    await service.compensate_create(
                        user_id,
                        str(getattr(prepared_item["prepared"], "operation_id", "")),
                    )
                except Exception:
                    logger.warning("Failed to compensate prepared managed item")
            raise ManagedStorageError("managed storage reservation failed") from exc
    first = managed_file_plan(prepared_items[0]["prepared"]) if prepared_items else ManagedFilePlan()
    return {
        "compat_service": service,
        "items": prepared_items,
        "file_id": first.file_id,
        "storage_key": first.storage_key,
        "status": "pending",
    }
    try:
        return await _invoke(
            service,
            ("reserve_group_create", "reserve_files", "reserve_create", "reserve"),
            user_id=user_id,
            source=source,
            items=items,
            idempotency_key=idempotency_key,
            operation_kind=operation_kind,
        )
    except ManagedStorageUnavailableError:
        raise
    except Exception as exc:
        raise ManagedStorageError("managed storage reservation failed") from exc


async def commit_managed_files(
    reservation: Any,
    *,
    user_id: str,
    items: list[dict[str, Any]],
) -> Any:
    """Commit staged items after their owner rows/pointers are durable."""

    service = _service_or_none()
    if service is None:
        raise ManagedStorageUnavailableError("managed storage service disappeared during commit")
    if isinstance(reservation, Mapping) and reservation.get("compat_service") is not None:
        compat_service = reservation["compat_service"]
        try:
            prepared_items = [item["prepared"] for item in reservation.get("items", [])]
            if not prepared_items:
                return None
            if all(bool(getattr(prepared, "reused", False)) for prepared in prepared_items):
                return prepared_items[0]
            complete_group = getattr(compat_service, "complete_group_create", None)
            if callable(complete_group):
                completed = await complete_group(prepared_items)
                return completed[0] if completed else prepared_items[0]
            # A single item can use the ordinary create finalizer. This branch is
            # retained for one-file WeCom/Skill callers and old test doubles.
            completed = await compat_service.complete_create(prepared_items[0])
            if getattr(prepared_items[0], "replaced_file_id", None):
                finalize_replace = getattr(compat_service, "finalize_replace", None)
                if not callable(finalize_replace):
                    raise ManagedStorageUnavailableError(
                        "managed storage replacement finalizer is unavailable"
                    )
                await finalize_replace(prepared_items[0])
            return completed[0] if completed else prepared_items[0]
        except Exception as exc:
            raise ManagedStorageError("managed storage commit failed") from exc
    try:
        return await _invoke(
            service,
            ("commit_group_create", "commit_files", "commit_reservation", "commit"),
            reservation=reservation,
            user_id=user_id,
            items=items,
        )
    except Exception as exc:
        if isinstance(exc, ManagedStorageError):
            raise
        raise ManagedStorageError("managed storage commit failed") from exc


async def compensate_managed_files(
    reservation: Any,
    *,
    user_id: str,
    items: list[dict[str, Any]],
    reason: str,
) -> Any:
    """Compensate a reservation without guessing at unknown physical keys."""

    service = _service_or_none()
    if service is None:
        raise ManagedStorageUnavailableError("managed storage service disappeared during compensation")
    if isinstance(reservation, Mapping) and reservation.get("compat_service") is not None:
        compat_service = reservation["compat_service"]
        try:
            prepared_items = [item["prepared"] for item in reservation.get("items", [])]
            operation_ids = {
                str(getattr(prepared, "operation_id", "")) for prepared in prepared_items
            }
            for operation_id in operation_ids:
                if operation_id:
                    await compat_service.compensate_create(user_id, operation_id)
            return None
        except Exception as exc:
            raise ManagedStorageError("managed storage compensation failed") from exc
    try:
        return await _invoke(
            service,
            ("compensate_group_create", "compensate_files", "compensate_reservation", "compensate"),
            reservation=reservation,
            user_id=user_id,
            items=items,
            reason=reason[:2048],
        )
    except Exception as exc:
        if isinstance(exc, ManagedStorageError):
            raise
        raise ManagedStorageError("managed storage compensation failed") from exc


async def release_managed_file(
    *,
    user_id: str,
    file_id: str | None = None,
    storage_key: str | None = None,
    source: str,
    reason: str,
    allow_protected: bool = False,
) -> Any | None:
    """Release one owned logical file; historical refs never block this call."""

    service = _service_or_none()
    if service is None:
        return None
    try:
        return await _invoke(
            service,
            ("delete_file", "release_file", "logical_delete_file", "delete_owned_file"),
            user_id=user_id,
            file_id=file_id,
            storage_key=storage_key,
            source=source,
            reason=reason[:2048],
            allow_protected=allow_protected,
        )
    except Exception as exc:
        if isinstance(exc, ManagedStorageError):
            raise
        raise ManagedStorageError("managed storage release failed") from exc


async def resolve_managed_attachment_statuses(
    *,
    user_id: str,
    attachments: list[dict[str, Any]],
) -> list[Any] | None:
    """Resolve statuses in one bounded batch for Agent/history projections."""

    service = _service_or_none()
    if service is None:
        return None
    identifiers = [
        {
            "file_id": item.get("file_id") or item.get("fileId"),
            "key": item.get("key"),
        }
        for item in attachments
    ]
    status_for_user = getattr(service, "status_for_user", None)
    if callable(status_for_user):
        file_ids = [str(item.get("file_id") or item.get("fileId")) for item in attachments if item.get("file_id") or item.get("fileId")]
        keys = [str(item.get("key")) for item in attachments if item.get("key")]
        try:
            result = await status_for_user(user_id, file_ids, keys)
        except Exception as exc:
            raise ManagedStorageError("managed attachment status lookup failed") from exc
        return list(result or [])
    try:
        result = await _invoke(
            service,
            ("resolve_attachment_statuses", "batch_get_status", "get_file_statuses"),
            user_id=user_id,
            attachments=attachments,
            identifiers=identifiers,
        )
    except Exception as exc:
        if isinstance(exc, ManagedStorageError):
            raise
        raise ManagedStorageError("managed attachment status lookup failed") from exc
    if result is None:
        return []
    if isinstance(result, Mapping):
        return [result]
    return list(result)


async def register_managed_message_refs(
    *,
    event_id: str,
    session_id: str,
    user_id: str,
    file_ids: list[str],
) -> bool:
    """Register idempotent `(event,file_id)` refs when the foundation supports it."""

    service = _service_or_none()
    if service is None or not file_ids:
        return False
    try:
        result = await _invoke(
            service,
            ("register_message_refs", "add_message_refs", "register_attachment_refs"),
            event_id=event_id,
            session_id=session_id,
            user_id=user_id,
            file_ids=list(dict.fromkeys(file_ids)),
        )
    except Exception as exc:
        logger.warning("Managed message-reference registration failed: %s", exc)
        return False
    return result is not False


async def remove_managed_message_refs(*, session_id: str, user_id: str) -> bool:
    """Remove session refs only; never invoke logical delete or physical purge."""

    service = _service_or_none()
    if service is None:
        return False
    try:
        result = await _invoke(
            service,
            ("remove_session_message_refs", "delete_session_message_refs", "remove_message_refs"),
            session_id=session_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Managed session message-reference cleanup failed: %s", exc)
        return False
    return result is not False


__all__ = [
    "ManagedFilePlan",
    "ManagedStorageError",
    "ManagedStorageUnavailableError",
    "commit_managed_files",
    "compensate_managed_files",
    "get_managed_storage_service",
    "has_explicit_managed_storage_service",
    "managed_file_plan",
    "managed_file_plan_for",
    "register_managed_message_refs",
    "release_managed_file",
    "remove_managed_message_refs",
    "reserve_managed_files",
    "resolve_managed_attachment_statuses",
    "set_managed_storage_service",
]

# Keep the module importable in static tooling even when the optional foundation is
# not present yet; adapters use this explicit seam rather than importing collections.

__all__ = sorted(set(__all__))

# Silence an over-eager linter that otherwise treats ``inspect`` as unused when the
# runtime supplies a C-extension callable without a signature.
_ = inspect
