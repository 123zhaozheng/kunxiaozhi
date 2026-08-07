"""
Trace Storage - 按 trace 聚合事件存储

将同一 trace_id 的所有事件聚合到一条 MongoDB 文档中，
大幅减少文档数量，同时保留完整的事件上下文。

数据结构:
{
    "trace_id": "xxx",
    "session_id": "xxx",
    "run_id": "xxx",
    "agent_id": "xxx",
    "user_id": "xxx",
    "events": [
        {"seq": 1, "event_type": "message:chunk", "data": {...}, "timestamp": ...},
        {"seq": 2, "event_type": "thinking", "data": {...}, "timestamp": ...},
    ],
    "event_count": 2,
    "started_at": ISODate,
    "updated_at": ISODate,
    "completed_at": ISODate,
    "status": "running" | "completed" | "error",
    "metadata": {}
}

全局序号说明:
- 每个 session 有一个独立的递增序号计数器 (存储在 session_events_counter 集合)
- 每个事件写入时获取全局序号，用于断点续读
"""

import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, cast

from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError

from src.infra.logging import get_logger
from src.infra.session.history_cursor import (
    HISTORY_ORDERING_VERSION,
    InvalidHistoryCursor,
    decode_history_cursor,
    encode_history_cursor,
    event_ordering_key,
    filter_fingerprint,
)
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now, utc_now_iso
from src.kernel.config import settings

logger = get_logger(__name__)


class TraceWriteUnavailableError(RuntimeError):
    """Raised when trace writes are disabled while uniqueness is not ready."""


class TraceIdentityConflictError(ValueError):
    """Raised when a trace id is reused for a different session/run."""

_SESSION_EVENTS_BATCH_SIZE = 200
SESSION_EVENT_FILTER_LIST_LIMIT = 100
TRACE_EVENTS_DEFAULT_LIMIT = 1000
# Upper bound for a single read_session_events call. Must match the API layer's
# SESSION_EVENT_RESPONSE_LIMIT_MAX (10000) so the frontend can request the full
# history without being silently truncated here. A long team/SOP session can
# easily exceed the old 5000 cap, which made replies vanish on refresh.
TRACE_EVENTS_READ_LIMIT = 10000
TRACE_LIST_LIMIT = 100
TRACE_STALE_RECOVERY_TERMINAL_EVENTS = ("done", "error", "complete")


def _get_session_event_read_default_limit() -> int:
    configured = max(int(getattr(settings, "SESSION_EVENT_READ_DEFAULT_LIMIT", 1000) or 0), 1)
    return min(configured, TRACE_EVENTS_READ_LIMIT)


def _clamp_positive_int(value: int | None, *, default: int, maximum: int) -> int:
    try:
        candidate = int(value if value is not None else default)
    except (TypeError, ValueError):
        candidate = default
    return min(max(candidate, 1), maximum)


def _clamp_event_read_limit(value: int | None, *, default: int) -> int:
    try:
        candidate = int(value if value is not None else default)
    except (TypeError, ValueError):
        candidate = default
    if candidate <= 0:
        return 0
    return min(candidate, TRACE_EVENTS_READ_LIMIT)


def _clamp_nonnegative_int(value: int | None) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _bounded_unique_strings(
    values: Optional[List[str]],
    limit: int = SESSION_EVENT_FILTER_LIST_LIMIT,
) -> List[str]:
    if not values:
        return []
    bounded: List[str] = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        bounded.append(value)
        if len(bounded) >= limit:
            break
    return bounded


class TraceStorage:
    """
    Trace 存储类

    按 trace_id 聚合事件，使用 MongoDB $push 追加事件到数组。
    写入时按 Redis 顺序追加，读取时按 started_at 排序后合并。
    """

    def __init__(self):
        self._collection = None
        self._counter_collection = None
        self._event_collection = None
        self._merger = None  # 事件合并器
        self._indexes_lock = asyncio.Lock()
        self._indexes_task: Optional[asyncio.Task[bool]] = None
        # ``None`` means index initialization has not been attempted yet. This
        # preserves direct storage usage in tests while startup gates production
        # traffic on the explicit readiness result.
        self._indexes_ready: Optional[bool] = None
        self._index_errors: Dict[str, str] = {}
        self._index_attempts = 0
        self._duplicate_trace_ids: List[str] = []
        self._reconcile_metrics: Dict[str, int] = {
            "scanned": 0,
            "reconciled": 0,
            "skipped_active_heartbeat": 0,
            "skipped_running_task": 0,
            "skipped_current_run": 0,
            "skipped_no_terminal_evidence": 0,
            "cas_conflicts": 0,
            "failures": 0,
        }

    @property
    def stale_recovery_metrics(self) -> Dict[str, int]:
        """Return counters from the most recent stale-trace recovery pass."""
        return dict(self._reconcile_metrics)

    @property
    def index_status(self) -> Dict[str, Any]:
        """Return an immutable snapshot suitable for readiness/health output."""
        return {
            "ready": self._indexes_ready is True,
            "attempted": self._indexes_ready is not None,
            "attempts": self._index_attempts,
            "errors": dict(self._index_errors),
            "duplicate_trace_ids": list(self._duplicate_trace_ids),
        }

    @property
    def collection(self):
        """延迟加载 MongoDB 集合"""
        if self._collection is None:
            client = get_mongo_client()
            db = client[settings.MONGODB_DB]
            self._collection = db[settings.MONGODB_TRACES_COLLECTION]
            # 索引创建在首次异步操作时触发，避免在 property getter 中调用 create_task
        return self._collection

    @property
    def counter_collection(self):
        """session 级事件全局序号计数器集合（延迟加载）"""
        if self._counter_collection is None:
            client = get_mongo_client()
            db = client[settings.MONGODB_DB]
            self._counter_collection = db["session_events_counter"]
        return self._counter_collection

    @property
    def event_collection(self):
        """Immutable one-event-per-document collection."""
        if self._event_collection is None:
            client = get_mongo_client()
            self._event_collection = client[settings.MONGODB_DB][
                getattr(settings, "MONGODB_TRACE_EVENTS_COLLECTION", "trace_events")
            ]
        return self._event_collection

    async def ensure_event_indexes(self) -> bool:
        """Create the idempotency and cursor indexes for ``trace_events``."""
        collection = self.event_collection
        indexes: tuple[tuple[str, list[tuple[str, int]], dict[str, Any]], ...] = (
            ("session_trace_event_unique", [("session_id", 1), ("trace_id", 1), ("event_id", 1)], {"unique": True}),
            ("session_seq_event_idx", [("session_id", 1), ("seq", 1), ("event_id", 1)], {}),
            ("session_run_seq_event_idx", [("session_id", 1), ("run_id", 1), ("seq", 1), ("event_id", 1)], {}),
            ("trace_type_timestamp_idx", [("trace_id", 1), ("event_type", 1), ("timestamp", 1)], {}),
        )
        try:
            for name, keys, kwargs in indexes:
                await collection.create_index(keys, name=name, background=True, **kwargs)
            return True
        except Exception as exc:
            logger.error("trace_events index initialization failed: %s", exc)
            return False

    async def write_trace_events(self, events: List[Dict[str, Any]]) -> Any:
        """Idempotently upsert immutable event documents.

        ``$setOnInsert`` guarantees retries with an existing event id do not
        mutate its sequence or payload. Callers retain the batch when this
        raises so transient Mongo failures are retryable.
        """
        if not events:
            return None
        operations = []
        seen_identities: set[tuple[str, str, str]] = set()
        for event in events:
            identity = {
                "session_id": event["session_id"],
                "trace_id": event["trace_id"],
                "event_id": event["event_id"],
            }
            identity_key = (identity["session_id"], identity["trace_id"], identity["event_id"])
            if identity_key in seen_identities:
                continue
            seen_identities.add(identity_key)
            doc = dict(event)
            doc.setdefault("created_at", utc_now())
            operations.append(UpdateOne(identity, {"$setOnInsert": doc}, upsert=True))
        return await self.event_collection.bulk_write(operations, ordered=False)

    async def get_event_store_session_events(self, session_id: str, **filters: Any) -> List[Dict[str, Any]]:
        """Read immutable events, normalized to the history cursor contract.

        ``trace_events`` deliberately stores event data separately from trace
        metadata.  A completed-only read therefore has to join against the
        trace collection; filtering on an event document's status would be
        incorrect because status is mutable metadata and is not copied onto
        each immutable event.
        """
        query: Dict[str, Any] = {"session_id": session_id}
        if filters.get("completed_only", True):
            trace_query: Dict[str, Any] = {"session_id": session_id, "status": {"$ne": "running"}}
            if filters.get("run_id"):
                trace_query["run_id"] = filters["run_id"]
            if filters.get("exclude_run_id"):
                trace_query["run_id"] = {"$ne": filters["exclude_run_id"]}
            if filters.get("run_ids"):
                trace_query["run_id"] = {"$in": filters["run_ids"]}
            trace_cursor = self.collection.find(trace_query, {"trace_id": 1})
            completed_trace_ids: list[str] = []
            async for trace in trace_cursor:
                trace_id = trace.get("trace_id")
                if trace_id:
                    completed_trace_ids.append(str(trace_id))
            # Fail closed when metadata is missing: an immutable event without
            # a known terminal trace must not leak into completed history.
            if not completed_trace_ids:
                return []
            query["trace_id"] = {"$in": completed_trace_ids}
        if filters.get("run_id"):
            query["run_id"] = filters["run_id"]
        if filters.get("exclude_run_id"):
            query["run_id"] = {"$ne": filters["exclude_run_id"]}
        if filters.get("run_ids"):
            query["run_id"] = {"$in": filters["run_ids"]}
        if filters.get("event_types"):
            query["event_type"] = {"$in": filters["event_types"]}
        cursor = self.event_collection.find(query).sort([("seq", 1), ("event_id", 1)])
        events: List[Dict[str, Any]] = []
        async for event in cursor:
            event.pop("_id", None)
            events.append(event)
        events.sort(key=event_ordering_key)
        return events

    async def get_event_store_session_events_page(self, session_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Cursor pagination for the immutable event source."""
        events = await self.get_event_store_session_events(session_id, **kwargs)
        after = kwargs.get("after")
        if after:
            fingerprint = filter_fingerprint(
                scope=session_id,
                event_types=_bounded_unique_strings(kwargs.get("event_types"), SESSION_EVENT_FILTER_LIST_LIMIT),
                run_id=kwargs.get("run_id"), exclude_run_id=kwargs.get("exclude_run_id"),
                run_ids=_bounded_unique_strings(kwargs.get("run_ids"), SESSION_EVENT_FILTER_LIST_LIMIT),
            )
            key = decode_history_cursor(after, scope=session_id, fingerprint=fingerprint)
            events = [event for event in events if event_ordering_key(event) > key]
        limit = _clamp_event_read_limit(kwargs.get("limit"), default=_get_session_event_read_default_limit())
        has_more = len(events) > limit
        page = events[:limit]
        fingerprint = filter_fingerprint(
            scope=session_id,
            event_types=_bounded_unique_strings(kwargs.get("event_types"), SESSION_EVENT_FILTER_LIST_LIMIT),
            run_id=kwargs.get("run_id"), exclude_run_id=kwargs.get("exclude_run_id"),
            run_ids=_bounded_unique_strings(kwargs.get("run_ids"), SESSION_EVENT_FILTER_LIST_LIMIT),
        )
        next_cursor = encode_history_cursor(scope=session_id, fingerprint=fingerprint, key=event_ordering_key(page[-1])) if has_more and page else None
        return {"events": page, "has_more": has_more, "next_cursor": next_cursor,
                "history_complete": not has_more, "ordering_version": HISTORY_ORDERING_VERSION,
                "events_limited": has_more, "events_limit": kwargs.get("limit")}

    @staticmethod
    def legacy_event_id(trace_id: str, ordinal: int, event: Dict[str, Any]) -> str:
        """Derive a stable id for an event copied from a legacy array."""
        canonical = json.dumps(
            [trace_id, ordinal, event.get("event_type"), event.get("timestamp"), event.get("data")],
            ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str,
        )
        return "legacy-" + hashlib.sha256(canonical.encode()).hexdigest()

    async def backfill_legacy_events(
        self, *, batch_size: int = 100, dry_run: bool = True, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Replay legacy arrays into ``trace_events`` without mutating source data.

        The returned counters are suitable for an audit/coverage record. A
        truncated legacy array is reported as incomplete because backfill
        cannot reconstruct events already discarded by the old writer.
        """
        query: Dict[str, Any] = {}
        if session_id:
            query["session_id"] = session_id
        copied = 0
        traces = 0
        errors: List[str] = []
        pending: List[Dict[str, Any]] = []
        cursor = self.collection.find(query, {"trace_id": 1, "session_id": 1, "run_id": 1, "events": 1, "event_count": 1})
        async for trace in cursor:
            traces += 1
            trace_id = str(trace.get("trace_id") or "")
            sid = str(trace.get("session_id") or "")
            events = trace.get("events") or []
            expected_count = trace.get("event_count")
            if isinstance(expected_count, int) and expected_count > len(events):
                # The old array writer counted every event but retained only a
                # bounded tail.  Backfill cannot reconstruct the discarded
                # prefix, so surface an incomplete coverage result.
                errors.append(f"{trace_id or 'missing-trace'}:legacy_events_truncated")
            for ordinal, event in enumerate(events):
                if not isinstance(event, dict) or not trace_id or not sid:
                    errors.append(trace_id or "missing-trace")
                    continue
                pending.append({
                    "event_id": self.legacy_event_id(trace_id, ordinal, event),
                    "session_id": sid,
                    "trace_id": trace_id,
                    "run_id": trace.get("run_id"),
                    "seq": event.get("seq"),
                    "timestamp": event.get("timestamp"),
                    "event_type": event.get("event_type", ""),
                    "data": event.get("data", {}),
                })
                if len(pending) >= max(int(batch_size), 1):
                    if not dry_run:
                        await self.write_trace_events(pending)
                    copied += len(pending)
                    pending = []
        if pending:
            if not dry_run:
                await self.write_trace_events(pending)
            copied += len(pending)
        return {"dry_run": dry_run, "traces": traces, "copied": copied,
                "errors": errors, "complete": not errors}

    async def next_event_seq(self, session_id: str) -> int:
        """
        原子递增并返回该 session 的下一个事件全局序号。

        跨 trace 的所有事件共享同一个 session 级计数器，因此 seq 单调递增、
        能稳定表达事件的因果先后——避免按 timestamp 排序时并发/同毫秒事件
        顺序不稳定导致的消息错位与重复渲染。
        """
        doc = await self.counter_collection.find_one_and_update(
            {"_id": session_id},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=True,
        )
        return int(doc.get("seq", 1))

    async def ensure_indexes_if_needed(self) -> bool:
        """Await index initialization, coalescing concurrent callers.

        A failed attempt leaves readiness false and the next call starts a new
        attempt. This makes transient Mongo failures retryable without allowing
        writes to proceed under an unknown uniqueness contract.
        """
        if self._indexes_ready is True:
            return True
        async with self._indexes_lock:
            task = self._indexes_task
            if task is None or task.done():
                task = asyncio.create_task(self._initialize_indexes())
                self._indexes_task = task
        try:
            ready = await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._indexes_ready = False
            self._index_errors.setdefault("__initialization__", str(exc))
            return False
        self._indexes_ready = bool(ready)
        if ready:
            self._start_merger()
        return bool(ready)

    async def _initialize_indexes(self) -> bool:
        """Create each index independently, with bounded transient retries."""
        self._index_attempts += 1
        self._index_errors = {}
        self._duplicate_trace_ids = []
        collection = self.collection
        regular_indexes = [
            (
                "session_status_started_at_idx",
                [("session_id", 1), ("status", 1), ("started_at", 1)],
            ),
            ("session_run_status_idx", [("session_id", 1), ("run_id", 1), ("status", 1)]),
            ("started_at_idx", [("started_at", -1)]),
            ("session_started_at_desc_idx", [("session_id", 1), ("started_at", -1)]),
            ("status_merged_idx", [("status", 1), ("metadata.merged", 1)]),
        ]
        for name, keys in regular_indexes:
            try:
                await self._create_index_with_retry(collection, name, keys)
            except Exception as exc:
                self._index_errors[name] = str(exc)

        unique_name = "trace_id_unique_idx"
        try:
            self._duplicate_trace_ids = await self._find_duplicate_trace_ids(collection)
            if self._duplicate_trace_ids:
                self._index_errors[unique_name] = (
                    f"duplicate trace_id values: {len(self._duplicate_trace_ids)}"
                )
            else:
                await self._create_index_with_retry(
                    collection, unique_name, [("trace_id", 1)], unique=True
                )
        except Exception as exc:
            self._index_errors[unique_name] = str(exc)

        ready = not self._index_errors and not self._duplicate_trace_ids
        if ready:
            logger.info("MongoDB indexes ensured for trace_storage")
        else:
            logger.error(
                "Trace storage index readiness failed: errors=%s duplicates=%d",
                self._index_errors,
                len(self._duplicate_trace_ids),
            )
        return ready

    async def _ensure_indexes(self) -> bool:
        """Backward-compatible explicit initializer used by maintenance/tests."""
        return await self._initialize_indexes()

    async def _create_index_with_retry(
        self,
        collection: Any,
        name: str,
        keys: List[tuple[str, int]],
        **kwargs: Any,
    ) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                await collection.create_index(keys, name=name, background=True, **kwargs)
                return
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.05 * (2**attempt))
        assert last_error is not None
        raise last_error

    async def _find_duplicate_trace_ids(self, collection: Any) -> List[str]:
        pipeline = [
            {"$match": {"trace_id": {"$exists": True, "$ne": None}}},
            {"$group": {"_id": "$trace_id", "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
            {"$project": {"_id": 1}},
            {"$limit": 1000},
        ]
        rows = await collection.aggregate(pipeline).to_list(length=1000)
        return [str(row["_id"]) for row in rows if row.get("_id") is not None]

    def _start_merger(self):
        """启动事件合并器"""
        if not settings.ENABLE_EVENT_MERGER:
            logger.info("EventMerger disabled by configuration")
            return

        if self._merger is None:
            try:
                from src.infra.session.event_merger import get_event_merger

                self._merger = get_event_merger(self)
                self._merger.start()
                logger.info("EventMerger started successfully")
            except Exception as e:
                logger.warning(f"Failed to start EventMerger: {e}")

    async def create_trace(
        self,
        trace_id: str,
        session_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        创建 trace 文档（幂等：若已存在则跳过）

        Args:
            trace_id: 唯一 trace 标识
            session_id: 会话 ID
            agent_id: Agent ID
            run_id: 运行 ID
            user_id: 用户 ID
            metadata: 额外元数据

        Returns:
            是否创建成功（已存在也返回 True）
        """
        if self._indexes_ready is False:
            raise TraceWriteUnavailableError(
                "trace writes are disabled until trace indexes become ready"
            )
        now = utc_now()
        doc: Dict[str, Any] = {
            "trace_id": trace_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "user_id": user_id,
            "events": [],
            "event_count": 0,
            "started_at": now,
            "updated_at": now,
            "status": "running",
            "metadata": metadata or {},
        }

        try:
            # $setOnInsert makes concurrent callers converge on one document and
            # prevents a later partial create from overwriting initial metadata.
            try:
                result = await self.collection.update_one(
                    {"trace_id": trace_id},
                    {"$setOnInsert": doc},
                    upsert=True,
                )
            except TypeError:
                # A small compatibility fallback for legacy test doubles and
                # rolling deployments whose collection wrapper lacks upsert.
                result = await self.collection.insert_one(doc)
                logger.info(
                    "Created trace %s for session %s, inserted_id=%s",
                    trace_id,
                    session_id,
                    result.inserted_id,
                )
                return True

            upserted_id = getattr(result, "upserted_id", None)
            if upserted_id is not None:
                logger.info("Created trace %s for session %s", trace_id, session_id)
                return True

            find_one = getattr(self.collection, "find_one", None)
            if find_one is None:
                await self._merge_trace_metadata_if_missing(trace_id, metadata or {})
                return True
            try:
                existing = await find_one(
                    {"trace_id": trace_id},
                    {"session_id": 1, "run_id": 1, "metadata": 1},
                )
            except TypeError:
                existing = await find_one({"trace_id": trace_id})
            if existing is None:
                # The document may have disappeared between upsert and read;
                # report failure rather than claiming an idempotent success.
                logger.error("Trace %s disappeared after idempotent upsert", trace_id)
                return False
            self._validate_trace_identity(existing, session_id, run_id, trace_id)
            await self._merge_trace_metadata_if_missing(trace_id, metadata or {})
            logger.debug("Trace %s already exists, merged missing metadata", trace_id)
            return True
        except DuplicateKeyError:
            # During rolling deployment a concurrent writer can still race an
            # index build. Validate the winner before treating this as success.
            find_one = getattr(self.collection, "find_one", None)
            if find_one is None:
                await self._merge_trace_metadata_if_missing(trace_id, metadata or {})
                return True
            existing = await find_one({"trace_id": trace_id})
            if existing is None:
                logger.error("Duplicate trace %s cannot be loaded for validation", trace_id)
                return False
            self._validate_trace_identity(existing, session_id, run_id, trace_id)
            await self._merge_trace_metadata_if_missing(trace_id, metadata or {})
            return True
        except TraceIdentityConflictError:
            raise
        except Exception as e:
            logger.error(f"Failed to create trace {trace_id}: {e}")
            import traceback

            traceback.print_exc()
            return False

    @staticmethod
    def _validate_trace_identity(
        existing: Dict[str, Any],
        session_id: str,
        run_id: Optional[str],
        trace_id: str,
    ) -> None:
        existing_session = existing.get("session_id")
        existing_run = existing.get("run_id")
        if existing_session and existing_session != session_id:
            raise TraceIdentityConflictError(
                f"trace_id {trace_id!r} belongs to session {existing_session!r}, "
                f"not {session_id!r}"
            )
        if run_id and existing_run and existing_run != run_id:
            raise TraceIdentityConflictError(
                f"trace_id {trace_id!r} belongs to run {existing_run!r}, not {run_id!r}"
            )

    async def _merge_trace_metadata_if_missing(
        self,
        trace_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Fill missing metadata fields on an existing trace (idempotent).

        Only sets keys that are absent or null/empty so we never overwrite
        values written by an earlier complete create.
        """
        if not metadata:
            return
        try:
            for key, value in metadata.items():
                if value is None or value == "":
                    continue
                field = f"metadata.{key}"
                await self.collection.update_one(
                    {
                        "trace_id": trace_id,
                        "$or": [
                            {field: {"$exists": False}},
                            {field: None},
                            {field: ""},
                        ],
                    },
                    {
                        "$set": {field: value},
                        "$currentDate": {"updated_at": True},
                    },
                )
        except Exception as e:
            logger.warning(
                "Failed to merge missing metadata for trace %s: %s", trace_id, e
            )

    async def append_event(
        self,
        trace_id: str,
        event_type: str,
        data: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> bool:
        """
        追加事件到 trace

        使用 $push 和 $inc 原子操作，保证一致性。

        Args:
            trace_id: Trace ID
            event_type: 事件类型
            data: 事件数据
            session_id: 会话 ID（用于分配 session 级全局序号 seq）

        Returns:
            是否追加成功
        """
        if self._indexes_ready is False:
            raise TraceWriteUnavailableError(
                "trace writes are disabled until trace indexes become ready"
            )
        try:
            seq = await self.next_event_seq(session_id) if session_id else None
            event_doc: Dict[str, Any] = {
                "event_type": event_type,
                "data": data,
                "timestamp": utc_now(),
            }
            if seq is not None:
                event_doc["seq"] = seq
            result = await self.collection.update_one(
                {"trace_id": trace_id},
                {
                    "$push": {"events": event_doc},
                    "$inc": {"event_count": 1},
                    "$set": {"updated_at": utc_now()},
                },
            )
            if result.modified_count == 0:
                logger.warning(f"append_event: trace {trace_id} not found or not modified")
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to append event to trace {trace_id}: {e}")
            return False

    async def _ensure_token_usage_event(self, trace_id: str) -> None:
        """Insert a zero token usage event before done when a trace has no usage event yet."""
        now = utc_now()
        usage_event = {
            "event_type": "token:usage",
            "data": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "duration": 0.0,
                "timestamp": utc_now_iso(),
            },
            "timestamp": now,
        }
        try:
            await self.collection.update_one(
                {
                    "trace_id": trace_id,
                    "events.event_type": {"$ne": "token:usage"},
                },
                [
                    {
                        "$set": {
                            "events": {
                                "$let": {
                                    "vars": {
                                        "done_index": {
                                            "$indexOfArray": ["$events.event_type", "done"]
                                        }
                                    },
                                    "in": {
                                        "$cond": [
                                            {"$gte": ["$$done_index", 0]},
                                            {
                                                "$concatArrays": [
                                                    {"$slice": ["$events", 0, "$$done_index"]},
                                                    [usage_event],
                                                    {
                                                        "$slice": [
                                                            "$events",
                                                            "$$done_index",
                                                            {
                                                                "$subtract": [
                                                                    {"$size": "$events"},
                                                                    "$$done_index",
                                                                ]
                                                            },
                                                        ]
                                                    },
                                                ]
                                            },
                                            {"$concatArrays": ["$events", [usage_event]]},
                                        ]
                                    },
                                }
                            },
                            "event_count": {"$add": [{"$ifNull": ["$event_count", 0]}, 1]},
                            "updated_at": now,
                        }
                    }
                ],
            )
        except Exception as e:
            logger.warning("Failed to ensure token usage event for trace %s: %s", trace_id, e)

    async def complete_trace(
        self,
        trace_id: str,
        status: str = "completed",
        metadata: Optional[Dict[str, Any]] = None,
        ensure_token_usage: bool = True,
    ) -> bool:
        """
        标记 trace 完成

        Args:
            trace_id: Trace ID
            status: 最终状态 (completed/error)
            metadata: 额外元数据

        Returns:
            是否更新成功
        """
        if self._indexes_ready is False:
            raise TraceWriteUnavailableError(
                "trace writes are disabled until trace indexes become ready"
            )
        update = {
            "$set": {
                "status": status,
                "completed_at": utc_now(),
                "updated_at": utc_now(),
            }
        }
        if metadata:
            for key, value in metadata.items():
                update["$set"][f"metadata.{key}"] = value

        try:
            if ensure_token_usage:
                await self._ensure_token_usage_event(trace_id)
            result = await self.collection.update_one(
                {"trace_id": trace_id},
                update,
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to complete trace {trace_id}: {e}")
            return False

    async def get_trace(
        self,
        trace_id: str,
        *,
        include_events: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        获取 trace 摘要，默认不加载大 events 数组。

        Args:
            trace_id: Trace ID
            include_events: 是否返回完整 events 数组

        Returns:
            trace 文档或 None
        """
        try:
            projection = {"_id": 0} if include_events else {"_id": 0, "events": 0}
            doc = await self.collection.find_one(
                {"trace_id": trace_id},
                projection,
            )
            return doc
        except Exception as e:
            logger.error(f"Failed to get trace {trace_id}: {e}")
            return None

    async def get_trace_events(
        self,
        trace_id: str,
        event_types: Optional[List[str]] = None,
        max_events: int = TRACE_EVENTS_DEFAULT_LIMIT,
    ) -> List[Dict[str, Any]]:
        """
        获取 trace 的事件列表

        Args:
            trace_id: Trace ID
            event_types: 可选的事件类型过滤
            max_events: 最大返回事件数，防止一次读取超大 trace

        Returns:
            事件列表
        """
        max_events = _clamp_event_read_limit(
            max_events,
            default=TRACE_EVENTS_DEFAULT_LIMIT,
        )
        if max_events <= 0:
            return []

        pipeline: List[Dict[str, Any]] = [
            {"$match": {"trace_id": trace_id}},
            {
                "$project": {
                    "events.event_type": 1,
                    "events.data": 1,
                    "events.timestamp": 1,
                }
            },
            {"$unwind": "$events"},
        ]
        if event_types:
            pipeline.append({"$match": {"events.event_type": {"$in": event_types}}})
        pipeline.append({"$limit": max_events})
        pipeline.append(
            {
                "$project": {
                    "_id": 0,
                    "event_type": "$events.event_type",
                    "data": "$events.data",
                    "timestamp": "$events.timestamp",
                }
            }
        )

        events: List[Dict[str, Any]] = []
        try:
            async for event in self.collection.aggregate(pipeline):
                events.append(event)
            return events
        except Exception as e:
            logger.error(f"Failed to get trace events for {trace_id}: {e}")
            return []

    async def get_first_trace_event(
        self,
        trace_id: str,
        event_types: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch the first matching event from one trace without loading the full events array."""
        pipeline: List[Dict[str, Any]] = [
            {"$match": {"trace_id": trace_id}},
            {
                "$project": {
                    "events.event_type": 1,
                    "events.data": 1,
                    "events.timestamp": 1,
                }
            },
            {"$unwind": "$events"},
        ]
        if event_types:
            pipeline.append({"$match": {"events.event_type": {"$in": event_types}}})
        pipeline.extend(
            [
                {"$limit": 1},
                {
                    "$project": {
                        "_id": 0,
                        "event_type": "$events.event_type",
                        "data": "$events.data",
                        "timestamp": "$events.timestamp",
                    }
                },
            ]
        )

        try:
            async for event in self.collection.aggregate(pipeline):
                return event
            return None
        except Exception as e:
            logger.error(f"Failed to get first trace event for {trace_id}: {e}")
            return None

    async def get_last_trace_event(
        self,
        trace_id: str,
        event_types: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch the latest matching event from one trace without returning the full events array."""
        pipeline: List[Dict[str, Any]] = [
            {"$match": {"trace_id": trace_id}},
            {
                "$project": {
                    "events.event_type": 1,
                    "events.data": 1,
                    "events.timestamp": 1,
                    "events.seq": 1,
                }
            },
            {"$unwind": "$events"},
        ]
        if event_types:
            pipeline.append({"$match": {"events.event_type": {"$in": event_types}}})
        pipeline.extend(
            [
                {"$sort": {"events.seq": -1, "events.timestamp": -1}},
                {"$limit": 1},
                {
                    "$project": {
                        "_id": 0,
                        "event_type": "$events.event_type",
                        "data": "$events.data",
                        "timestamp": "$events.timestamp",
                    }
                },
            ]
        )

        try:
            async for event in self.collection.aggregate(pipeline):
                return event
            return None
        except Exception as e:
            logger.error(f"Failed to get last trace event for {trace_id}: {e}")
            return None

    async def list_traces(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        列出 traces

        Args:
            session_id: 按会话过滤
            user_id: 按用户过滤
            agent_id: 按 Agent 过滤
            status: 按状态过滤
            limit: 最大数量
            skip: 跳过数量

        Returns:
            trace 列表（不含 events 数组，仅摘要）
        """
        limit = _clamp_positive_int(limit, default=50, maximum=TRACE_LIST_LIMIT)
        skip = _clamp_nonnegative_int(skip)
        query = {}
        if session_id:
            query["session_id"] = session_id
        if user_id:
            query["user_id"] = user_id
        if agent_id:
            query["agent_id"] = agent_id
        if status:
            query["status"] = status

        try:
            cursor = (
                self.collection.find(
                    query,
                    {
                        "_id": 0,
                        "events": 0,  # 排除大数组
                    },
                )
                .sort("started_at", -1)
                .skip(skip)
                .limit(limit)
            )
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Failed to list traces: {e}")
            return []

    async def list_run_summaries(
        self,
        session_id: str,
        limit: int = 50,
        skip: int = 0,
        trace_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出会话 run 摘要，并只投影第一条用户消息事件。"""
        limit = _clamp_positive_int(limit, default=50, maximum=TRACE_LIST_LIMIT)
        skip = _clamp_nonnegative_int(skip)
        query = {"session_id": session_id}
        if trace_id:
            query["trace_id"] = trace_id

        projection: Dict[str, Any] = {
            "_id": 0,
            "run_id": 1,
            "trace_id": 1,
            "agent_id": 1,
            "started_at": 1,
            "completed_at": 1,
            "status": 1,
            "event_count": 1,
            "events": {"$elemMatch": {"event_type": "user:message"}},
        }

        try:
            cursor = (
                self.collection.find(query, projection)
                .sort("started_at", -1)
                .skip(skip)
                .limit(limit)
            )
            traces = await cursor.to_list(length=limit)
            summaries: List[Dict[str, Any]] = []
            for trace in traces:
                user_message = None
                events = trace.get("events") or []
                if events:
                    data = events[0].get("data", {})
                    user_message = data.get("content") or data.get("message") or ""
                    if user_message and len(user_message) > 20:
                        user_message = user_message[:17] + "..."

                summaries.append(
                    {
                        "run_id": trace.get("run_id"),
                        "trace_id": trace.get("trace_id"),
                        "agent_id": trace.get("agent_id"),
                        "started_at": trace.get("started_at"),
                        "completed_at": trace.get("completed_at"),
                        "status": trace.get("status"),
                        "event_count": trace.get("event_count", 0),
                        "user_message": user_message,
                    }
                )
            return summaries
        except Exception as e:
            logger.error(f"Failed to list run summaries: {e}")
            return []

    async def reconcile_stale_running_traces(
        self,
        session_id: str | None = None,
        *,
        session_collection: Any | None = None,
        heartbeat_check: Callable[[str], Awaitable[bool]] | None = None,
        grace_seconds: int | None = None,
        batch_size: int | None = None,
    ) -> int:
        """Conservatively recover abandoned running traces outside the read path.

        A trace is a candidate only after the configured grace period. A live
        heartbeat always wins. Otherwise a terminal session task state is
        sufficient, or a persisted terminal event is accepted only when the
        owning task is no longer running. Every write includes the observed
        identity/timestamp and ``status=running`` so a concurrent writer wins
        the race instead of being overwritten.
        """
        if not getattr(settings, "TRACE_STALE_RECOVERY_ENABLED", True):
            logger.info("[Trace] stale-running recovery disabled")
            return 0
        grace_value: Any = (
            grace_seconds
            if grace_seconds is not None
            else getattr(settings, "TRACE_STALE_RECOVERY_GRACE_SECONDS", 120)
        )
        batch_value: Any = (
            batch_size
            if batch_size is not None
            else getattr(settings, "TRACE_STALE_RECOVERY_BATCH_SIZE", 100)
        )
        grace = max(int(cast(Any, grace_value)), 1)
        limit = min(max(int(cast(Any, batch_value)), 1), 1000)
        now = utc_now()
        cutoff = now - timedelta(seconds=grace)
        self._reconcile_metrics = {key: 0 for key in self._reconcile_metrics}

        if session_collection is None:
            try:
                client = get_mongo_client()
                session_collection = client[settings.MONGODB_DB][settings.MONGODB_SESSIONS_COLLECTION]
            except Exception as exc:
                self._reconcile_metrics["failures"] += 1
                logger.error("[Trace] stale-running recovery cannot load sessions: %s", exc, exc_info=True)
                return 0
        if heartbeat_check is None:
            async def _check(run_id: str) -> bool:
                from src.infra.task.heartbeat import TaskHeartbeat

                return await TaskHeartbeat().check_exists_strict(run_id)

            heartbeat_check = _check

        query: Dict[str, Any] = {
            "status": "running",
            "$or": [
                {"updated_at": {"$lt": cutoff}},
                {"updated_at": {"$exists": False}, "started_at": {"$lt": cutoff}},
            ],
        }
        if session_id:
            query["session_id"] = session_id
        try:
            cursor = self.collection.find(query).sort("updated_at", 1).limit(limit)
            candidates = await cursor.to_list(length=limit) if hasattr(cursor, "to_list") else [doc async for doc in cursor]
        except Exception as exc:
            self._reconcile_metrics["failures"] += 1
            logger.error("[Trace] stale-running candidate scan failed: %s", exc, exc_info=True)
            return 0

        reconciled = 0
        terminal_task_statuses = {"completed", "failed", "cancelled", "expired"}
        for trace in candidates[:limit]:
            self._reconcile_metrics["scanned"] += 1
            trace_id = str(trace.get("trace_id") or "")
            trace_session_id = str(trace.get("session_id") or session_id or "")
            run_id = str(trace.get("run_id") or "")
            if not trace_id or not trace_session_id or not run_id:
                self._reconcile_metrics["skipped_no_terminal_evidence"] += 1
                continue
            try:
                session = await session_collection.find_one(
                    {"session_id": trace_session_id},
                    {"metadata.task_status": 1, "metadata.current_run_id": 1},
                )
                if not session:
                    self._reconcile_metrics["skipped_current_run"] += 1
                    continue
                metadata = (session or {}).get("metadata") or {}
                task_status = str(metadata.get("task_status") or "")
                current_run_id = metadata.get("current_run_id")
                if not current_run_id or str(current_run_id) != run_id:
                    self._reconcile_metrics["skipped_current_run"] += 1
                    continue
                if await heartbeat_check(run_id):
                    self._reconcile_metrics["skipped_active_heartbeat"] += 1
                    continue
                terminal_events = {
                    str(event.get("event_type"))
                    for event in (trace.get("events") or [])
                    if isinstance(event, dict)
                }.intersection(TRACE_STALE_RECOVERY_TERMINAL_EVENTS)
                if task_status not in terminal_task_statuses and not terminal_events:
                    self._reconcile_metrics["skipped_no_terminal_evidence"] += 1
                    continue
                if task_status in {"starting", "queued", "pending", "cancelling"}:
                    self._reconcile_metrics["skipped_running_task"] += 1
                    continue
                reason = "terminal_task_status" if task_status in terminal_task_statuses else "heartbeat_timeout_with_terminal_event"
                terminal_status = "error" if (
                    task_status in {"failed", "cancelled", "expired"}
                    or "error" in terminal_events
                ) else "completed"
                observed = {
                    "task_status": task_status or None,
                    "current_run_id": str(current_run_id) if current_run_id else None,
                    "heartbeat": False,
                    "terminal_events": sorted(terminal_events),
                    "reconciled_status": terminal_status,
                    "observed_at": now,
                }
                update_now = utc_now()
                update = {
                    "$set": {
                        "status": terminal_status,
                        "completed_at": update_now,
                        "updated_at": update_now,
                        "reconciled_at": update_now,
                        "reconciled_reason": reason,
                        "reconciled_observed_state": observed,
                    }
                }
                cas_query: Dict[str, Any] = {
                    "session_id": trace_session_id,
                    "trace_id": trace_id,
                    "run_id": run_id,
                    "status": "running",
                }
                if trace.get("_id") is not None:
                    cas_query["_id"] = trace["_id"]
                if trace.get("updated_at") is not None:
                    cas_query["updated_at"] = trace["updated_at"]
                elif trace.get("started_at") is not None:
                    cas_query["started_at"] = trace["started_at"]
                result = None
                for attempt in range(2):
                    try:
                        result = await self.collection.update_one(cas_query, update)
                        break
                    except Exception:
                        if attempt == 1:
                            raise
                        await asyncio.sleep(0)
                        logger.warning("[Trace] retrying stale recovery CAS: trace=%s", trace_id)
                assert result is not None
                if result.modified_count > 0:
                    reconciled += 1
                    self._reconcile_metrics["reconciled"] += 1
                else:
                    self._reconcile_metrics["cas_conflicts"] += 1
                    logger.info("[Trace] stale recovery CAS lost race: trace=%s run=%s", trace_id, run_id)
            except Exception as exc:
                self._reconcile_metrics["failures"] += 1
                logger.error("[Trace] stale recovery failed: trace=%s: %s", trace_id, exc, exc_info=True)
        if reconciled:
            logger.info("[Trace] reconciled %d stale running trace(s); metrics=%s", reconciled, self._reconcile_metrics)
        return reconciled

    async def get_session_events(
        self,
        session_id: str,
        event_types: Optional[List[str]] = None,
        run_id: Optional[str] = None,
        exclude_run_id: Optional[str] = None,
        completed_only: bool = True,
        run_ids: Optional[List[str]] = None,
        max_events: Optional[int] = None,
        after: Optional[str] = None,
        _allow_probe: bool = False,
        _include_cursor_metadata: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        获取会话的所有事件（跨 traces 聚合）

        按 run 顺序（started_at）合并事件，每个 run 内的事件保持原有顺序。

        Args:
            session_id: 会话 ID
            event_types: 可选的事件类型过滤列表
            run_id: 可选的运行 ID 过滤（用于隔离多轮对话）
            exclude_run_id: 可选的运行 ID 排除（用于排除正在运行的 run）
            completed_only: 是否只返回成功完成的 trace 中的事件（默认 True）
            run_ids: 可选的运行 ID 列表过滤（用于部分分享等场景）
            max_events: 可选的最大返回事件数

        Returns:
            事件列表，按 run 顺序合并
        """
        try:
            event_types = _bounded_unique_strings(event_types, SESSION_EVENT_FILTER_LIST_LIMIT)
            run_ids = _bounded_unique_strings(run_ids, SESSION_EVENT_FILTER_LIST_LIMIT)
            # 构建查询条件
            match_query: Dict[str, Any] = {"session_id": session_id}
            if run_ids:
                match_query["run_id"] = {"$in": run_ids}
            elif run_id:
                match_query["run_id"] = run_id
            if exclude_run_id:
                match_query["run_id"] = {"$ne": exclude_run_id}
            # 排除正在运行的 trace（只返回 running 状态以外的）
            if completed_only:
                match_query["status"] = {"$ne": "running"}

            if max_events is None:
                max_events = _get_session_event_read_default_limit()
            else:
                max_events = _clamp_event_read_limit(
                    max_events,
                    default=_get_session_event_read_default_limit(),
                )
                if _allow_probe and max_events == TRACE_EVENTS_READ_LIMIT:
                    max_events += 1

            if max_events <= 0:
                return []

            pipeline: List[Dict[str, Any]] = [
                {"$match": match_query},
                {"$sort": {"started_at": 1}},
                {
                    "$project": {
                        "trace_id": 1,
                        "run_id": 1,
                        "events.event_type": 1,
                        "events.data": 1,
                        "events.timestamp": 1,
                        "events.seq": 1,
                        "events.event_id": 1,
                        "events.id": 1,
                    }
                },
                {"$unwind": {"path": "$events", "includeArrayIndex": "event_index"}},
            ]
            if event_types:
                pipeline.append({"$match": {"events.event_type": {"$in": event_types}}})
            # Normalize a total ordering. Missing seq values remain in the legacy
            # bucket; trace/event identity breaks timestamp ties deterministically.
            pipeline.append(
                {
                    "$set": {
                        "events.legacy_bucket": {
                            "$cond": [{"$isNumber": "$events.seq"}, 1, 0]
                        },
                        "events.seq_sort": {
                            "$cond": [{"$isNumber": "$events.seq"}, "$events.seq", 0]
                        },
                        "events.timestamp_sort": {"$ifNull": ["$events.timestamp", ""]},
                        "events.event_id_sort": {
                            "$ifNull": ["$events.event_id", {"$ifNull": ["$events.id", ""]}]
                        },
                        "events.event_index_sort": {"$ifNull": ["$event_index", 0]},
                    }
                }
            )
            if after:
                fingerprint = filter_fingerprint(
                    scope=session_id,
                    event_types=event_types,
                    run_id=run_id,
                    exclude_run_id=exclude_run_id,
                    run_ids=run_ids,
                )
                key = decode_history_cursor(after, scope=session_id, fingerprint=fingerprint)
                pipeline.append(
                    {
                        "$match": {
                            "$or": [
                                {"events.legacy_bucket": {"$gt": key[0]}},
                                {
                                    "events.legacy_bucket": key[0],
                                    "events.seq_sort": {"$gt": key[1]},
                                },
                                {
                                    "events.legacy_bucket": key[0],
                                    "events.seq_sort": key[1],
                                    "events.timestamp_sort": {"$gt": key[2]},
                                },
                                {
                                    "events.legacy_bucket": key[0],
                                    "events.seq_sort": key[1],
                                    "events.timestamp_sort": key[2],
                                    "trace_id": {"$gt": key[3]},
                                },
                                {
                                    "events.legacy_bucket": key[0],
                                    "events.seq_sort": key[1],
                                    "events.timestamp_sort": key[2],
                                    "trace_id": key[3],
                                    "events.event_id_sort": {"$gt": key[4]},
                                },
                                {
                                    "events.legacy_bucket": key[0],
                                    "events.seq_sort": key[1],
                                    "events.timestamp_sort": key[2],
                                    "trace_id": key[3],
                                    "events.event_id_sort": key[4],
                                    "events.event_index_sort": {"$gt": key[5]},
                                },
                            ]
                        }
                    }
                )
            pipeline.append(
                {
                    "$sort": {
                        "events.legacy_bucket": 1,
                        "events.seq_sort": 1,
                        "events.timestamp_sort": 1,
                        "trace_id": 1,
                        "events.event_id_sort": 1,
                        "events.event_index_sort": 1,
                    }
                }
            )
            pipeline.extend(
                [
                    {"$limit": max_events},
                    {
                        "$project": {
                            "_id": 0,
                            "trace_id": 1,
                            "run_id": 1,
                            "event_type": "$events.event_type",
                            "data": "$events.data",
                            "timestamp": "$events.timestamp",
                            "seq": "$events.seq",
                            "event_id": {"$ifNull": ["$events.event_id", "$events.id"]},
                            "_event_index": "$event_index",
                        }
                    },
                ]
            )

            events: List[Dict[str, Any]] = []
            async for event in self.collection.aggregate(pipeline):
                if not _include_cursor_metadata:
                    event.pop("_event_index", None)
                events.append(event)
            logger.debug(
                f"Session {session_id} (run_id={run_id}) returned {len(events)} bounded events"
            )
            return events
        except InvalidHistoryCursor:
            raise
        except Exception as e:
            logger.error(f"Failed to get session events: {e}")
            return []

    async def get_session_events_page(
        self,
        session_id: str,
        event_types: Optional[List[str]] = None,
        run_id: Optional[str] = None,
        exclude_run_id: Optional[str] = None,
        completed_only: bool = True,
        run_ids: Optional[List[str]] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read one cursor page while retaining the legacy list API."""
        page_limit = limit or _get_session_event_read_default_limit()
        page_limit = _clamp_event_read_limit(page_limit, default=_get_session_event_read_default_limit())
        # Probe one extra item. The extra slot is intentionally allowed above the
        # single-page safety cap so a request at 10,000 can report has_more exactly.
        probe = min(page_limit + 1, TRACE_EVENTS_READ_LIMIT + 1)
        events = await self.get_session_events(
            session_id,
            event_types,
            run_id=run_id,
            exclude_run_id=exclude_run_id,
            completed_only=completed_only,
            run_ids=run_ids,
            max_events=probe,
            after=after,
            _allow_probe=True,
            _include_cursor_metadata=True,
        )
        has_more = len(events) > page_limit
        page_events = events[:page_limit]
        fingerprint = filter_fingerprint(
            scope=session_id,
            event_types=_bounded_unique_strings(event_types, SESSION_EVENT_FILTER_LIST_LIMIT),
            run_id=run_id,
            exclude_run_id=exclude_run_id,
            run_ids=_bounded_unique_strings(run_ids, SESSION_EVENT_FILTER_LIST_LIMIT),
        )
        next_cursor = None
        if has_more and page_events:
            next_cursor = encode_history_cursor(
                scope=session_id,
                fingerprint=fingerprint,
                key=event_ordering_key(
                    page_events[-1],
                    ordinal=page_events[-1].get("_event_index"),
                ),
            )
        for event in page_events:
            event.pop("_event_index", None)
        return {
            "events": page_events,
            "has_more": has_more,
            "next_cursor": next_cursor,
            # Legacy trace arrays can already have been truncated by $slice or
            # buffer pressure. Reaching their end proves only that this source
            # has no more retained events, not that durable history is complete.
            "history_complete": False,
            "ordering_version": HISTORY_ORDERING_VERSION,
            "events_limited": has_more,
            "events_limit": limit,
        }
    async def get_run_events(
        self,
        session_id: str,
        run_id: str,
        event_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取特定 run 的事件

        Args:
            session_id: 会话 ID
            run_id: 运行 ID
            event_types: 可选的事件类型过滤列表

        Returns:
            事件列表，按写入顺序
        """
        return await self.get_session_events(session_id, event_types, run_id=run_id)

    async def delete_trace(self, trace_id: str) -> bool:
        """删除 trace"""
        try:
            result = await self.collection.delete_one({"trace_id": trace_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Failed to delete trace {trace_id}: {e}")
            return False

    async def delete_session_traces(self, session_id: str) -> int:
        """删除会话的所有 traces"""
        try:
            result = await self.collection.delete_many({"session_id": session_id})
            return result.deleted_count
        except Exception as e:
            logger.error(f"Failed to delete session traces: {e}")
            return 0


# Singleton
_trace_storage: Optional[TraceStorage] = None


def get_trace_storage() -> TraceStorage:
    """获取 TraceStorage 单例"""
    global _trace_storage
    if _trace_storage is None:
        _trace_storage = TraceStorage()
    return _trace_storage
