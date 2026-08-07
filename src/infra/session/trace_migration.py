"""Explicit, auditable migration of duplicate trace documents.

This module is intentionally separate from :mod:`trace_storage`: reads and
normal trace writes must never repair historical duplicates implicitly.  The
service accepts collections as constructor arguments, which also keeps dry-run
and failure-injection tests entirely offline.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.kernel.config import settings

logger = get_logger(__name__)

LEASE_SECONDS = 300
MIGRATION_OPERATION_COLLECTION = "trace_migration_operations"
MIGRATION_BACKUP_COLLECTION = "trace_migration_backups"
MIGRATION_AUDIT_COLLECTION = "trace_migration_audit"
MIGRATION_LEASE_COLLECTION = "trace_migration_leases"


class MigrationConfirmationRequiredError(ValueError):
    """Apply mode requires an explicit confirmation flag."""


class LeaseUnavailableError(RuntimeError):
    """Another maintenance operation owns the trace-group lease."""


class MigrationVerificationError(RuntimeError):
    """The merged document did not match its deterministic verification data."""


_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _validate_operation_id(operation_id: str) -> str:
    """Reject ambiguous operation ids before they reach bookkeeping queries."""
    if not isinstance(operation_id, str) or not _OPERATION_ID_RE.fullmatch(operation_id):
        raise ValueError("operation_id must be 1-128 safe ASCII characters")
    return operation_id


class DuplicateTraceGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    trace_id: str
    source_ids: list[Any]
    active: bool = False
    skip_reason: str | None = None
    event_count: int = 0
    merged_event_count: int = 0


class DuplicateTraceMigrationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    dry_run: bool
    duplicate_groups: list[DuplicateTraceGroup] = Field(default_factory=list)
    skipped_active_groups: int = 0
    source_document_count: int = 0
    merged_event_count: int = 0
    index_preflight_clear: bool | None = None


class DuplicateTraceMigrationResult(DuplicateTraceMigrationPlan):
    applied: bool = False
    rolled_back: bool = False
    backed_up_document_count: int = 0
    deleted_document_count: int = 0
    checksum: str | None = None
    state: str = "planned"
    errors: list[str] = Field(default_factory=list)


def _result_from_operation(document: Mapping[str, Any]) -> DuplicateTraceMigrationResult:
    """Load an operation record without leaking Mongo bookkeeping fields."""
    fields = set(DuplicateTraceMigrationResult.model_fields)
    return DuplicateTraceMigrationResult.model_validate(
        {key: value for key, value in document.items() if key in fields}
    )


@dataclass(frozen=True)
class _Group:
    session_id: str
    trace_id: str
    docs: tuple[dict[str, Any], ...]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stable(value: Any) -> Any:
    """Turn BSON-ish values into deterministic JSON-compatible values."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _stable(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(_stable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sort_value(value: Any) -> tuple[int, float, str]:
    """Return a comparable value for the mixed timestamp types in old traces."""
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, datetime):
        return (2, value.astimezone(timezone.utc).timestamp(), "")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (2, float(value), "")
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return (1, 0.0, text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (2, parsed.astimezone(timezone.utc).timestamp(), "")


def _source_sort_key(doc: Mapping[str, Any]) -> str:
    """Stable final tie-breaker for source documents and event processing."""
    return _canonical_json(doc.get("_id"))


def _ordered_sources(docs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Select canonical source by updated, completed, then lowest ``_id``.

    Stable sorts intentionally put the lowest id first before applying the
    descending timestamp keys.  This avoids reversing the id tie-breaker when
    timestamps are equal.
    """
    ordered = sorted((dict(doc) for doc in docs), key=_source_sort_key)
    ordered = sorted(ordered, key=lambda doc: _sort_value(doc.get("completed_at")), reverse=True)
    return sorted(ordered, key=lambda doc: _sort_value(doc.get("updated_at")), reverse=True)


def _event_identity(event: Mapping[str, Any]) -> str:
    event_id = event.get("event_id") or event.get("id")
    if event_id:
        return f"event:{event_id}"
    # Match the legacy trace_events backfill identity: fields outside this
    # tuple (for example a transient source marker) must not create duplicates.
    legacy_key = [event.get("seq"), event.get("event_type"), event.get("timestamp"), event.get("data")]
    return "legacy:" + _checksum(legacy_key)


def _event_sort_key(
    event: Mapping[str, Any],
    *,
    trace_id: str = "",
    source_id: str = "",
    ordinal: int = 0,
) -> tuple[int, int, str, str, str, str, int]:
    """Return the migration's stable, session-history ordering key.

    Legacy events (those without a numeric sequence) deliberately sort before
    sequenced events.  This is the same legacy bucket used by the history
    cursor, and avoids making a migrated trace reorder its old events merely
    because a duplicate document was repaired.  Source id and array ordinal
    are only final tie-breakers; they are never exposed as event data.
    """
    seq = event.get("seq")
    has_seq = isinstance(seq, (int, float)) and not isinstance(seq, bool)
    seq_number = int(seq) if isinstance(seq, (int, float)) and not isinstance(seq, bool) else 0
    return (
        1 if has_seq else 0,
        seq_number,
        _canonical_json(event.get("timestamp")),
        trace_id,
        _event_identity(event),
        source_id,
        ordinal,
    )


def _merge_documents(docs: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], int]:
    if not docs:
        raise ValueError("cannot merge an empty trace group")
    ordered = _ordered_sources(docs)
    canonical = dict(ordered[0])
    metadata: dict[str, Any] = {}
    for doc in ordered:
        candidate = doc.get("metadata")
        if isinstance(candidate, Mapping):
            for key in sorted(candidate, key=str):
                if key not in metadata or metadata[key] in (None, ""):
                    metadata[key] = candidate[key]
    if metadata:
        canonical["metadata"] = metadata

    events_by_id: dict[str, tuple[dict[str, Any], str, int]] = {}
    for source_doc in ordered:
        source_id = _source_sort_key(source_doc)
        for ordinal, event in enumerate(source_doc.get("events") or []):
            if not isinstance(event, Mapping):
                continue
            identity = _event_identity(event)
            event_copy: dict[str, Any] = {str(key): value for key, value in event.items()}
            if not event_copy.get("event_id"):
                if event_copy.get("id"):
                    # ``id`` is an older spelling of the immutable event id;
                    # preserve it instead of turning it into a legacy hash.
                    event_copy["event_id"] = event_copy["id"]
                else:
                    event_copy["event_id"] = "legacy-" + identity.removeprefix("legacy:")
            events_by_id.setdefault(identity, (event_copy, source_id, ordinal))
    events = [
        item[0]
        for item in sorted(
            events_by_id.values(),
            key=lambda item: _event_sort_key(
                item[0], source_id=item[1], ordinal=item[2]
            ),
        )
    ]
    canonical["events"] = events
    logical_counts = [
        value
        for source_doc in ordered
        for value in [source_doc.get("event_count")]
        if isinstance(value, int) and value >= 0
    ]
    logical_count = max([len(events), *logical_counts])
    canonical["event_count"] = logical_count
    return canonical, logical_count


async def _cursor_to_list(cursor: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        page_values = await cursor.to_list(length=limit)
        return [dict(value) for value in page_values]
    if isinstance(cursor, list):
        return [dict(value) for value in cursor]
    values: list[dict[str, Any]] = []
    async for value in cursor:
        values.append(dict(value))
        if limit is not None and len(values) >= limit:
            break
    return values


async def _find(collection: Any, query: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    return await _cursor_to_list(collection.find(dict(query or {})))


async def _find_one(collection: Any, query: Mapping[str, Any]) -> dict[str, Any] | None:
    value = await collection.find_one(dict(query))
    return dict(value) if value is not None else None


class TraceDuplicateMigrator:
    """Plan/apply/rollback duplicate repair with explicit safety barriers."""

    def __init__(
        self,
        collection: Any,
        *,
        backup_collection: Any | None = None,
        audit_collection: Any | None = None,
        operation_collection: Any | None = None,
        lease_collection: Any | None = None,
        lease_seconds: int = LEASE_SECONDS,
    ) -> None:
        self.collection = collection
        self.backup_collection = backup_collection
        self.audit_collection = audit_collection
        self.operation_collection = operation_collection
        self.lease_collection = lease_collection
        self.lease_seconds = max(lease_seconds, 1)
        self._local_leases: set[str] = set()
        self._lease_tokens: dict[str, tuple[str, datetime]] = {}

    async def _duplicate_groups(self, *, session_id: str | None = None) -> list[_Group]:
        query = {"session_id": session_id} if session_id else {}
        docs = await _find(self.collection, query)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for doc in docs:
            sid, tid = doc.get("session_id"), doc.get("trace_id")
            if sid is None or tid is None:
                continue
            grouped.setdefault((str(sid), str(tid)), []).append(doc)
        return [
            _Group(sid, tid, tuple(sorted(items, key=_source_sort_key)))
            for (sid, tid), items in sorted(grouped.items())
            if len(items) > 1
        ]

    def _is_active(self, docs: Iterable[Mapping[str, Any]]) -> bool:
        now = _now().timestamp()
        for doc in docs:
            if str(doc.get("status", "")).lower() == "running":
                return True
            for field in ("heartbeat_at", "last_heartbeat", "heartbeat"):
                heartbeat = doc.get(field)
                if heartbeat is None:
                    continue
                if isinstance(heartbeat, bool):
                    if heartbeat:
                        return True
                    continue
                parsed = _sort_value(heartbeat)
                # A timestamp older than the lease window is no longer proof
                # of an active writer.  Unknown heartbeat formats fail closed.
                if parsed[0] == 2 and now - parsed[1] <= self.lease_seconds:
                    return True
                if parsed[0] != 2:
                    return True
        return False

    async def plan(
        self,
        *,
        operation_id: str | None = None,
        session_id: str | None = None,
        include_active: bool = False,
    ) -> DuplicateTraceMigrationPlan:
        operation_id = _validate_operation_id(
            operation_id if operation_id is not None else uuid.uuid4().hex
        )
        groups: list[DuplicateTraceGroup] = []
        skipped = 0
        event_total = 0
        for group in await self._duplicate_groups(session_id=session_id):
            active = self._is_active(group.docs)
            if active and not include_active:
                skipped += 1
            merged, count = _merge_documents(group.docs)
            event_total += count
            groups.append(
                DuplicateTraceGroup(
                    session_id=group.session_id,
                    trace_id=group.trace_id,
                    source_ids=[doc.get("_id") for doc in group.docs],
                    active=active,
                    skip_reason="active_writer" if active and not include_active else None,
                    event_count=sum(len(doc.get("events") or []) for doc in group.docs),
                    merged_event_count=int(merged.get("event_count") or 0),
                )
            )
        return DuplicateTraceMigrationPlan(
            operation_id=operation_id,
            dry_run=True,
            duplicate_groups=groups,
            skipped_active_groups=skipped,
            source_document_count=sum(len(group.source_ids) for group in groups),
            merged_event_count=event_total,
            # The deployed trace uniqueness gate is global (trace_id), so a
            # session-scoped plan must not claim readiness while another
            # session still has duplicates.
            index_preflight_clear=not bool(await self._duplicate_groups()),
        )

    @asynccontextmanager
    async def _lease(self, session_id: str, trace_id: str, operation_id: str) -> AsyncIterator[None]:
        key = f"{session_id}:{trace_id}"
        if key in self._local_leases:
            raise LeaseUnavailableError(f"trace group lease already held: {key}")
        self._local_leases.add(key)
        token = f"{operation_id}:{uuid.uuid4().hex}"
        acquired = True
        now = _now()
        if self.lease_collection is not None:
            acquired = False
            query = {"_id": key, "$or": [{"expires_at": {"$lte": now}}, {"token": token}]}
            update = {
                "$set": {"token": token, "operation_id": operation_id, "expires_at": now + timedelta(seconds=self.lease_seconds)},
                "$setOnInsert": {"session_id": session_id, "trace_id": trace_id, "created_at": now},
            }
            try:
                result = await self.lease_collection.update_one(query, update, upsert=True)
                acquired = bool(getattr(result, "upserted_id", None) is not None or getattr(result, "modified_count", 0))
            except Exception:
                # A duplicate insert means another migrator won the lease.
                acquired = False
            if not acquired:
                existing = await _find_one(self.lease_collection, {"_id": key, "token": token})
                acquired = existing is not None
        if not acquired:
            self._local_leases.discard(key)
            raise LeaseUnavailableError(f"trace group lease unavailable: {key}")
        self._lease_tokens[key] = (token, now + timedelta(seconds=self.lease_seconds))
        try:
            yield
        finally:
            if self.lease_collection is not None:
                try:
                    await self.lease_collection.delete_one({"_id": key, "token": token})
                except Exception as exc:
                    logger.warning("failed to release trace migration lease %s: %s", key, exc)
            self._lease_tokens.pop(key, None)
            self._local_leases.discard(key)

    async def _assert_lease(self, session_id: str, trace_id: str) -> None:
        """Fail closed if the maintenance lease expired during a long merge."""
        key = f"{session_id}:{trace_id}"
        state = self._lease_tokens.get(key)
        if state is None or state[1] <= _now():
            raise LeaseUnavailableError(f"trace group lease expired: {key}")
        if self.lease_collection is not None:
            current = await _find_one(
                self.lease_collection,
                {"_id": key, "token": state[0]},
            )
            if current is None or _sort_value(current.get("expires_at"))[1] <= _now().timestamp():
                raise LeaseUnavailableError(f"trace group lease expired: {key}")

    async def _write_operation(self, operation_id: str, update: Mapping[str, Any]) -> None:
        if self.operation_collection is None:
            return
        payload = {"operation_id": operation_id, "updated_at": _now(), **dict(update)}
        try:
            await self.operation_collection.update_one(
                {"_id": operation_id},
                {"$set": payload, "$setOnInsert": {"created_at": _now()}},
                upsert=True,
            )
        except (AttributeError, TypeError):
            await self.operation_collection.insert_one({"_id": operation_id, **payload})

    async def _backup(self, operation_id: str, docs: Sequence[Mapping[str, Any]]) -> None:
        if self.backup_collection is None:
            raise RuntimeError("backup collection is required for apply mode")
        existing = await _find(self.backup_collection, {"operation_id": operation_id})
        existing_ids = {_canonical_json(doc.get("source_id")) for doc in existing}
        missing = [
            {
                "_id": _checksum([operation_id, doc.get("_id")]),
                "operation_id": operation_id,
                "source_id": doc.get("_id"),
                "source": dict(doc),
                "created_at": _now(),
            }
            for doc in docs
            if _canonical_json(doc.get("_id")) not in existing_ids
        ]
        if missing:
            await self.backup_collection.insert_many(missing, ordered=True)

    async def _audit(self, result: DuplicateTraceMigrationResult) -> None:
        if self.audit_collection is None:
            return
        await self.audit_collection.insert_one(
            {"operation_id": result.operation_id, "kind": "trace_duplicate_migration", **result.model_dump(), "created_at": _now()}
        )

    async def apply(
        self,
        *,
        operation_id: str | None = None,
        session_id: str | None = None,
        confirm: bool = False,
        include_active: bool = False,
    ) -> DuplicateTraceMigrationResult:
        if not confirm:
            raise MigrationConfirmationRequiredError("apply requires confirm=True")
        if self.backup_collection is None:
            raise RuntimeError("backup collection is required for apply mode")
        operation_id = _validate_operation_id(
            operation_id if operation_id is not None else uuid.uuid4().hex
        )
        existing_operation = await _find_one(self.operation_collection, {"_id": operation_id}) if self.operation_collection is not None else None
        if existing_operation and existing_operation.get("state") in {"applied", "rolled_back"}:
            requested_session = existing_operation.get("requested_session_id")
            if requested_session is not None and requested_session != session_id:
                raise ValueError("operation_id belongs to a different session scope")
            return _result_from_operation(existing_operation)
        await self._write_operation(
            operation_id,
            {"state": "planning", "requested_session_id": session_id},
        )
        planned = await self.plan(operation_id=operation_id, session_id=session_id, include_active=include_active)
        result = DuplicateTraceMigrationResult(
            **{key: value for key, value in planned.model_dump().items() if key != "dry_run"},
            dry_run=False,
            applied=False,
        )
        all_backed_up: list[dict[str, Any]] = []
        merged_documents: list[dict[str, Any]] = []
        try:
            for item in planned.duplicate_groups:
                if item.active and not include_active:
                    continue
                async with self._lease(item.session_id, item.trace_id, operation_id):
                    docs = await _find(self.collection, {"session_id": item.session_id, "trace_id": item.trace_id})
                    if len(docs) < 2 or (self._is_active(docs) and not include_active):
                        continue
                    if any(doc.get("_id") is None for doc in docs):
                        raise MigrationVerificationError(
                            f"trace group contains a source without _id: "
                            f"{item.session_id}/{item.trace_id}"
                        )
                    merged, event_count = _merge_documents(docs)
                    merged_documents.append(merged)
                    await self._backup(operation_id, docs)
                    all_backed_up.extend(docs)
                    await self._write_operation(
                        operation_id,
                        {
                            "state": "backed_up",
                            "session_id": item.session_id,
                            "trace_id": item.trace_id,
                            "source_ids": [doc.get("_id") for doc in docs],
                        },
                    )
                    canonical_id = merged.get("_id")
                    # The marker is written before replacing or deleting any
                    # source.  It makes a failed non-transactional operation
                    # discoverable and safe to resume.
                    marker = {"merge_operation_id": operation_id, "merged_into": canonical_id}
                    merged_metadata = merged.setdefault("metadata", {})
                    if isinstance(merged_metadata, dict):
                        merged_metadata.update(marker)
                    await self._write_operation(operation_id, {"state": "merge_started", **marker})
                    await self._assert_lease(item.session_id, item.trace_id)
                    await self.collection.replace_one({"_id": canonical_id}, merged, upsert=False)
                    stored = await _find_one(self.collection, {"_id": canonical_id})
                    if stored is None:
                        raise MigrationVerificationError(f"canonical trace disappeared: {canonical_id}")
                    if _checksum(stored) != _checksum(merged):
                        raise MigrationVerificationError(f"merged document checksum mismatch: {canonical_id}")
                    if stored.get("event_count") != event_count or len(stored.get("events") or []) != len(merged.get("events") or []):
                        raise MigrationVerificationError(f"event count mismatch: {canonical_id}")
                    await self._write_operation(
                        operation_id,
                        {
                            "state": "merge_verified",
                            "checksum": _checksum(merged),
                            "event_count": event_count,
                        },
                    )
                    # The audit record is durable before the first source
                    # deletion.  A final record is appended after completion
                    # with the exact counts/checksum.
                    await self._audit(result)
                    delete_ids = [doc.get("_id") for doc in docs if doc.get("_id") != canonical_id]
                    # Re-read after the merge and immediately before deletion.
                    # A lease protects cooperating writers, while this check
                    # also fails closed if an older writer does not know about
                    # the maintenance lease.
                    latest_docs = await _find(
                        self.collection,
                        {"session_id": item.session_id, "trace_id": item.trace_id},
                    )
                    await self._assert_lease(item.session_id, item.trace_id)
                    latest_by_id = {doc.get("_id"): doc for doc in latest_docs}
                    expected_ids = {doc.get("_id") for doc in docs}
                    if set(latest_by_id) != expected_ids or (self._is_active(latest_docs) and not include_active):
                        raise MigrationVerificationError(
                            f"trace group changed while migrating: {item.session_id}/{item.trace_id}"
                        )
                    for source_doc in docs:
                        source_id = source_doc.get("_id")
                        if source_id != canonical_id and _checksum(latest_by_id[source_id]) != _checksum(source_doc):
                            raise MigrationVerificationError(f"source changed while migrating: {source_id}")
                    for source_id in delete_ids:
                        await self._assert_lease(item.session_id, item.trace_id)
                        delete_result = await self.collection.delete_one(
                            {"_id": source_id, "session_id": item.session_id, "trace_id": item.trace_id}
                        )
                        if getattr(delete_result, "deleted_count", 0) != 1:
                            raise MigrationVerificationError(f"source deletion mismatch: {source_id}")
                        result.deleted_document_count += 1
                    result.merged_event_count += event_count
            result.applied = True
            result.state = "applied"
            result.backed_up_document_count = len(all_backed_up)
            result.checksum = _checksum(merged_documents)
            result.index_preflight_clear = not bool(await self._duplicate_groups())
            await self._write_operation(operation_id, {"state": "applied", **result.model_dump()})
            await self._audit(result)
            return result
        except Exception as exc:
            result.errors.append(str(exc))
            result.state = "failed"
            await self._write_operation(operation_id, {"state": "failed", "errors": result.errors})
            raise

    async def rollback(self, operation_id: str) -> DuplicateTraceMigrationResult:
        if self.backup_collection is None:
            raise RuntimeError("backup collection is required for rollback")
        operation_id = _validate_operation_id(operation_id)
        existing_operation = (
            await _find_one(self.operation_collection, {"_id": operation_id})
            if self.operation_collection is not None
            else None
        )
        if existing_operation and existing_operation.get("state") == "rolled_back":
            return _result_from_operation(existing_operation)
        backups = await _find(self.backup_collection, {"operation_id": operation_id})
        if not backups:
            raise ValueError(f"no backup found for operation {operation_id}")
        restored = 0
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for backup in backups:
            source = dict(backup.get("source") or {})
            session_id, trace_id = source.get("session_id"), source.get("trace_id")
            if session_id is None or trace_id is None:
                raise MigrationVerificationError(
                    f"backup {backup.get('_id')} has no trace identity"
                )
            grouped.setdefault((str(session_id), str(trace_id)), []).append(source)
        for (session_id, trace_id), sources in grouped.items():
            async with self._lease(session_id, trace_id, operation_id):
                await self._assert_lease(session_id, trace_id)
                current = await _find(
                    self.collection,
                    {"session_id": session_id, "trace_id": trace_id},
                )
                if self._is_active(current):
                    raise MigrationVerificationError(
                        f"cannot rollback active trace group: {session_id}/{trace_id}"
                    )
                for source in sources:
                    await self._assert_lease(session_id, trace_id)
                    source_id = source.get("_id")
                    if source_id is None:
                        continue
                    await self.collection.replace_one(
                        {
                            "_id": source_id,
                            "session_id": session_id,
                            "trace_id": trace_id,
                        },
                        source,
                        upsert=True,
                    )
                    restored += 1
        await self._write_operation(operation_id, {"state": "rolled_back", "restored_document_count": restored})
        result = DuplicateTraceMigrationResult(
            operation_id=operation_id,
            dry_run=False,
            applied=False,
            rolled_back=True,
            state="rolled_back",
            backed_up_document_count=restored,
        )
        await self._audit(result)
        return result


TraceDuplicateMigration = TraceDuplicateMigrator


def _production_migrator() -> TraceDuplicateMigrator:
    db = get_mongo_client()[settings.MONGODB_DB]
    return TraceDuplicateMigrator(
        db[settings.MONGODB_TRACES_COLLECTION],
        backup_collection=db[MIGRATION_BACKUP_COLLECTION],
        audit_collection=db[MIGRATION_AUDIT_COLLECTION],
        operation_collection=db[MIGRATION_OPERATION_COLLECTION],
        lease_collection=db[MIGRATION_LEASE_COLLECTION],
    )


async def migrate_duplicate_traces(
    *,
    apply: bool = False,
    confirm: bool = False,
    session_id: str | None = None,
    operation_id: str | None = None,
    include_active: bool = False,
) -> DuplicateTraceMigrationPlan | DuplicateTraceMigrationResult:
    migrator = _production_migrator()
    if not apply:
        return await migrator.plan(
            operation_id=operation_id,
            session_id=session_id,
            include_active=include_active,
        )
    return await migrator.apply(
        operation_id=operation_id,
        session_id=session_id,
        confirm=confirm,
        include_active=include_active,
    )


async def rollback_duplicate_trace_migration(operation_id: str) -> DuplicateTraceMigrationResult:
    return await _production_migrator().rollback(operation_id)


async def _cli_async(args: argparse.Namespace) -> int:
    result: DuplicateTraceMigrationPlan | DuplicateTraceMigrationResult
    if args.rollback:
        result = await rollback_duplicate_trace_migration(args.rollback)
    else:
        result = await migrate_duplicate_traces(
            apply=args.apply,
            confirm=args.confirm,
            session_id=args.session_id,
            operation_id=args.operation_id,
            include_active=args.include_active,
        )
    print(json.dumps(result.model_dump(), ensure_ascii=False, default=str, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely migrate duplicate trace documents")
    parser.add_argument("--apply", action="store_true", help="mutate data; otherwise perform a dry-run")
    parser.add_argument("--confirm", action="store_true", help="required with --apply")
    parser.add_argument("--session-id")
    parser.add_argument("--operation-id")
    parser.add_argument(
        "--include-active",
        action="store_true",
        help="allow groups with a running/heartbeat writer (requires --apply --confirm)",
    )
    parser.add_argument("--rollback", metavar="OPERATION_ID")
    args = parser.parse_args(argv)
    if args.rollback and (args.apply or args.confirm):
        parser.error("--rollback cannot be combined with --apply/--confirm")
    if args.confirm and not args.apply:
        parser.error("--confirm requires --apply")
    if args.include_active and not args.apply:
        parser.error("--include-active requires --apply --confirm")
    try:
        return asyncio.run(_cli_async(args))
    except (MigrationConfirmationRequiredError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
