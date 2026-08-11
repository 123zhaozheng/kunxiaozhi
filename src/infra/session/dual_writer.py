"""
Dual Event Writer - 双写事件到 Redis Stream + MongoDB

所有事件按 trace_id 聚合到 MongoDB，大幅减少文档数量。
- Redis: 所有事件立即写入，保证 SSE 实时性
- MongoDB: 批量缓冲写入，确保数据不丢失

性能优化:
- 使用 bulk_write 批量更新 MongoDB，减少 DB 往返
- 分离 Redis/Mongo 锁，减少锁竞争
- 使用 asyncio.Event 替代轮询标志
"""

import asyncio
import json
import time
import uuid
from collections import OrderedDict, defaultdict
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from pymongo import UpdateOne

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger
from src.infra.session.history_cursor import (
    HISTORY_COMPAT_ORDERING_VERSION,
    HISTORY_ORDERING_VERSION,
    decode_history_cursor,
    encode_history_cursor,
    event_ordering_key,
    filter_fingerprint,
    history_ordering_key,
)
from src.infra.session.trace_storage import (
    TRACE_EVENTS_READ_LIMIT,
    TraceIdentityConflictError,
    TraceStorage,
    TraceWriteUnavailableError,
    get_trace_storage,
)
from src.infra.storage.redis import RedisStorage
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

logger = get_logger(__name__)


# MongoDB 批量写入配置
_MONGO_FLUSH_INTERVAL = 1.0  # 每 1000ms 刷新一次
_MONGO_BATCH_SIZE = 200  # 每 200 条立即刷新
_MONGO_BUFFER_MAX = 10000  # buffer 上限，防止 MongoDB 慢/宕机时 OOM
_TTL_SET_KEYS_MAX = 5000  # _ttl_set_keys 上限，防止内存泄漏
_LIVE_STREAM_READ_TIMEOUT_SECONDS = 24 * 60 * 60
_SSE_HEARTBEAT_INTERVAL_SECONDS = 15
_REDIS_XREAD_BLOCK_MS = 5000
_REDIS_REPLAY_BATCH_SIZE = 500
MongoBufferItem = tuple[str, str, dict, str, Optional[str], datetime, str, Optional[int]]


def _get_max_events_per_trace() -> int:
    """获取单个 trace 最多保留的事件数（可配置）"""
    return getattr(settings, "SESSION_MAX_EVENTS_PER_TRACE", 10000)


def _get_mongo_buffer_max() -> int:
    return max(int(getattr(settings, "SESSION_EVENT_MONGO_BUFFER_MAX", _MONGO_BUFFER_MAX) or 0), 1)


def _get_ttl_set_keys_max() -> int:
    return max(int(getattr(settings, "SESSION_EVENT_TTL_CACHE_MAX", _TTL_SET_KEYS_MAX) or 0), 1)


def _get_ttl_refresh_interval() -> float:
    ttl_seconds = max(int(getattr(settings, "SSE_CACHE_TTL", 86400) or 0), 1)
    return max(min(ttl_seconds / 2, 300.0), 1.0)


def _get_redis_replay_batch_size() -> int:
    return max(
        int(
            getattr(settings, "SESSION_EVENT_REDIS_REPLAY_BATCH_SIZE", _REDIS_REPLAY_BATCH_SIZE)
            or 0
        ),
        1,
    )


def _event_write_mode() -> str:
    return str(getattr(settings, "TRACE_EVENT_WRITE_MODE", "legacy") or "legacy").lower()


def _event_read_mode() -> str:
    return str(getattr(settings, "TRACE_EVENT_READ_MODE", "legacy") or "legacy").lower()


async def _serialize_event_data_for_redis(data: Any) -> str:
    if isinstance(data, dict):
        return await run_blocking_io(json.dumps, data, ensure_ascii=False)
    return str(data)


async def _parse_event_data_from_redis(data: Any) -> Any:
    if isinstance(data, str):
        try:
            return await run_blocking_io(json.loads, data)
        except json.JSONDecodeError:
            return data
    return data


def _build_mongo_bulk_operations(
    batch: list[MongoBufferItem],
    *,
    now: datetime,
    max_events: int,
    seqs_by_session: Optional[Dict[str, List[int]]] = None,
) -> list[UpdateOne]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    # Per-session cursor into the pre-allocated seq list, advanced in batch order
    # so each event gets the next seq for its session.
    seq_cursor: dict[str, int] = {}

    for item in batch:
        trace_id, event_type, data, session_id, run_id, timestamp = item[:6]
        event_id = item[6] if len(item) > 6 else uuid.uuid4().hex
        event_doc: dict = {
            # Keep the compatibility array keyed by the same immutable id so
            # dual-read can replace (rather than duplicate) each event.
            "event_id": event_id,
            "event_type": event_type,
            "data": data,
            "timestamp": timestamp,
        }
        if seqs_by_session and session_id and session_id in seqs_by_session:
            seqs = seqs_by_session[session_id]
            idx = seq_cursor.get(session_id, 0)
            if idx < len(seqs):
                event_doc["seq"] = seqs[idx]
                seq_cursor[session_id] = idx + 1
        grouped[trace_id].append(event_doc)

    operations: list[UpdateOne] = []
    for trace_id, events in grouped.items():
        operations.append(
            UpdateOne(
                {"trace_id": trace_id},
                {
                    "$push": {
                        "events": {
                            "$each": events,
                            "$slice": -max_events,
                        }
                    },
                    "$inc": {"event_count": len(events)},
                    "$set": {"updated_at": now},
                },
                # Trace documents are created and identity-validated through
                # TraceStorage before this bulk update.  Keeping this update
                # non-upserting prevents buffered events from bypassing the
                # readiness gate or creating a trace with a conflicting
                # session/run identity.
                upsert=False,
            )
        )
    return operations


def _build_trace_event_documents(
    batch: list[MongoBufferItem],
    *,
    seqs_by_session: Optional[Dict[str, List[int]]] = None,
) -> list[dict[str, Any]]:
    """Normalize buffered records into immutable event documents."""
    seq_cursor: dict[str, int] = {}
    documents: list[dict[str, Any]] = []
    for item in batch:
        trace_id, event_type, data, session_id, run_id, timestamp = item[:6]
        event_id = item[6] if len(item) > 6 else uuid.uuid4().hex
        seq = item[7] if len(item) > 7 else None
        if seq is None and seqs_by_session and session_id in seqs_by_session:
            index = seq_cursor.get(session_id, 0)
            values = seqs_by_session[session_id]
            if index < len(values):
                seq = values[index]
                seq_cursor[session_id] = index + 1
        document = {
            "event_id": event_id,
            "session_id": session_id,
            "trace_id": trace_id,
            "run_id": run_id,
            "seq": seq,
            "timestamp": timestamp,
            "event_type": event_type,
            "data": data,
        }
        documents.append(document)
    return documents


class DualEventWriter:
    """
    双写事件到 Redis Stream + MongoDB (Trace 模式)

    - Redis: 所有事件立即写入，保证 SSE 实时性
    - MongoDB: 批量缓冲写入，使用 Lock 保护，确保数据不丢失

    性能优化:
    - Redis 和 MongoDB 操作使用不同的锁，减少争用
    - 使用 asyncio.Event 替代轮询标志，避免 busy wait
    - 使用 bulk_write 批量更新 MongoDB
    """

    def __init__(self):
        self._redis = None
        self._trace = None
        self._ttl_set_keys: OrderedDict[str, float] = OrderedDict()
        # MongoDB 批量写入缓冲
        # (trace_id, event_type, data, session_id, run_id, timestamp)
        self._mongo_buffer: list[MongoBufferItem] = []
        self._mongo_lock = asyncio.Lock()  # 只保护 buffer 和 flush 操作
        self._flush_event = asyncio.Event()  # 使用 Event 替代轮询标志
        self._flush_event.set()  # 初始状态为已就绪
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_task_waiting = False

    @property
    def redis(self) -> RedisStorage:
        if self._redis is None:
            self._redis = RedisStorage()
        return self._redis

    @property
    def trace(self) -> TraceStorage:
        if self._trace is None:
            self._trace = get_trace_storage()
        return self._trace

    def _stream_key(self, session_id: str, run_id: Optional[str] = None) -> str:
        if run_id:
            return f"session:{session_id}:run:{run_id}:events"
        return f"session:{session_id}:events"

    async def create_trace(
        self,
        trace_id: str,
        session_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        # Presenter creation is a write path in its own right.  Do not rely on
        # the background bulk flusher to perform readiness checks later; that
        # would allow a trace document to be created before the uniqueness
        # preflight has completed.
        if not await self.trace.ensure_indexes_if_needed():
            raise TraceWriteUnavailableError(
                "trace writes are disabled until trace indexes become ready"
            )
        return await self.trace.create_trace(
            trace_id=trace_id,
            session_id=session_id,
            agent_id=agent_id,
            run_id=run_id,
            user_id=user_id,
            metadata=metadata,
        )

    async def write_event(
        self,
        session_id: str,
        event_type: str,
        data: Dict[str, Any],
        trace_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> bool:
        """
        双写事件到 Redis + MongoDB

        - Redis: 立即写入（无锁）
        - MongoDB: 缓冲写入，批量刷新（使用 Event 触发）
        """
        # 统一时间戳，确保 Redis 和 MongoDB 使用相同的时间
        timestamp = utc_now()
        event_id = event_id or uuid.uuid4().hex
        seq: Optional[int] = None
        if trace_id and _event_write_mode() in {"dual", "event_store"}:
            next_seq = getattr(self.trace, "next_event_seq", None)
            if next_seq is not None:
                seq = await next_seq(session_id)

        # ---- Redis 写入（立即，无锁） ----
        stream_key = self._stream_key(session_id, run_id)
        fields = {
            "event_id": event_id,
            "event_type": event_type,
            "data": await _serialize_event_data_for_redis(data),
            "timestamp": timestamp.isoformat(),
        }
        redis_success = await self._write_to_redis_direct(stream_key, fields)

        # ---- MongoDB 写入（缓冲，使用 Event 触发） ----
        if trace_id:
            should_flush_now = False
            while True:
                async with self._mongo_lock:
                    mongo_buffer_max = _get_mongo_buffer_max()
                    buffer_size = len(self._mongo_buffer)
                    if buffer_size < mongo_buffer_max:
                        if buffer_size >= int(mongo_buffer_max * 0.8):
                            logger.warning("MongoDB event buffer at %d/%d", buffer_size, mongo_buffer_max)
                        self._mongo_buffer.append(
                            (trace_id, event_type, data, session_id, run_id, timestamp, event_id, seq)
                        )
                        break
                # Backpressure rather than silently dropping old events.
                await self.flush_mongo_buffer()
            async with self._mongo_lock:
                buffer_size = len(self._mongo_buffer)
                # 防止 buffer 无限增长（MongoDB 慢/宕机时丢弃最旧的事件）
                if buffer_size >= mongo_buffer_max:
                    raise RuntimeError("MongoDB event buffer remained full after backpressure flush")
                # 当缓冲区达到 80% 时发出警告
                # 达到批量大小立即刷新
                if len(self._mongo_buffer) >= _MONGO_BATCH_SIZE:
                    should_flush_now = True
                # 使用 Event 触发延迟刷新
                elif self._flush_event.is_set():
                    self._flush_event.clear()
                    self._flush_task = asyncio.create_task(self._schedule_flush())
                    self._flush_task.add_done_callback(self._on_flush_task_done)

            if should_flush_now:
                await self.flush_mongo_buffer()

        return redis_success

    def _on_flush_task_done(self, task: asyncio.Task[None]) -> None:
        if self._flush_task is task:
            self._flush_task = None
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.warning("Scheduled MongoDB event flush failed: %s", exc)

    async def _schedule_flush(self) -> None:
        """调度延迟刷新"""
        try:
            self._flush_task_waiting = True
            await asyncio.sleep(_MONGO_FLUSH_INTERVAL)
        finally:
            self._flush_task_waiting = False
        await self._do_flush()

    async def _drain_scheduled_flush_task(self) -> bool:
        task = self._flush_task
        if task is None:
            return False
        if task is asyncio.current_task():
            return False
        if task.done():
            if self._flush_task is task:
                self._flush_task = None
            return False

        if self._flush_task_waiting:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if self._flush_task is task:
                self._flush_task = None
            return False

        try:
            await task
        except asyncio.CancelledError:
            return False
        except Exception as e:
            logger.warning("Scheduled MongoDB event flush failed while draining: %s", e)
            return False
        finally:
            if self._flush_task is task:
                self._flush_task = None
        return True

    async def _do_flush(self) -> None:
        """实际执行批量写入，使用 bulk_write 优化"""
        async with self._mongo_lock:
            if not self._mongo_buffer:
                self._flush_event.set()
                return

            batch = self._mongo_buffer
            self._mongo_buffer = []

        now = utc_now()
        max_events = _get_max_events_per_trace()

        # A buffered event is itself a trace write.  Ensure every trace has
        # passed the same readiness and identity checks as Presenter.create_trace
        # before issuing the bulk update; otherwise a delayed flush could create
        # traces while the unique index is unavailable.
        trace_context: dict[str, tuple[str, Optional[str]]] = {}
        for item in batch:
            trace_id, _event_type, _data, session_id, run_id, _timestamp = item[:6]
            identity = (session_id, run_id)
            previous = trace_context.setdefault(trace_id, identity)
            if previous != identity:
                async with self._mongo_lock:
                    self._mongo_buffer = batch + self._mongo_buffer
                    self._flush_event.set()
                raise TraceIdentityConflictError(
                    f"trace_id {trace_id!r} has conflicting session/run identities "
                    f"{previous!r} and {identity!r}"
                )

        ensure_indexes = getattr(self.trace, "ensure_indexes_if_needed", None)
        if ensure_indexes is not None:
            if not await ensure_indexes():
                # Put the batch back so a later readiness retry can persist it.
                async with self._mongo_lock:
                    self._mongo_buffer = batch + self._mongo_buffer
                    self._flush_event.set()
                raise TraceWriteUnavailableError(
                    "trace writes are disabled until trace indexes become ready"
                )

        create_trace = getattr(self.trace, "create_trace", None)
        if create_trace is not None:
            try:
                for trace_id, (session_id, run_id) in trace_context.items():
                    if not session_id:
                        raise TraceIdentityConflictError(
                            f"trace_id {trace_id!r} has no session identity"
                        )
                    created = await create_trace(
                        trace_id=trace_id,
                        session_id=session_id,
                        run_id=run_id,
                    )
                    if not created:
                        raise RuntimeError(f"failed to ensure trace {trace_id!r}")
            except Exception:
                # Do not silently drop events when the trace preflight fails.
                async with self._mongo_lock:
                    self._mongo_buffer = batch + self._mongo_buffer
                    self._flush_event.set()
                raise

        # Pre-allocate a session-level global sequence number for every event in
        # the batch, grouped by session (one atomic $inc per session). seq gives
        # a stable, monotonic global order across all traces of a session so that
        # reads can sort by seq instead of by (unreliable, same-millisecond)
        # timestamp — fixing cross-run event reordering that caused duplicate
        # message ids and wrong message order in the chat history.
        seqs_by_session: Dict[str, List[int]] = {}
        session_counts: Dict[str, int] = {}
        for item in batch:
            _trace_id, _event_type, _data, session_id, _run_id, _ts = item[:6]
            existing_seq = item[7] if len(item) > 7 else None
            if session_id and existing_seq is None:
                session_counts[session_id] = session_counts.get(session_id, 0) + 1
        for session_id, count in session_counts.items():
            try:
                doc = await self.trace.counter_collection.find_one_and_update(
                    {"_id": session_id},
                    {"$inc": {"seq": count}},
                    upsert=True,
                    return_document=True,
                )
                base = int(doc.get("seq", count))
                seqs_by_session[session_id] = list(
                    range(base - count + 1, base + 1)
                )
            except Exception as e:
                if _event_write_mode() in {"dual", "event_store"}:
                    async with self._mongo_lock:
                        self._mongo_buffer = batch + self._mongo_buffer
                        self._flush_event.set()
                    raise
                logger.warning(
                    f"Failed to allocate event seq for session {session_id}: {e}"
                )
                seqs_by_session[session_id] = []

        if _event_write_mode() in {"dual", "event_store"}:
            event_documents = await run_blocking_io(
                _build_trace_event_documents, batch, seqs_by_session=seqs_by_session
            )
            try:
                ensure_events = getattr(self.trace, "ensure_event_indexes", None)
                if ensure_events is not None and not await ensure_events():
                    raise TraceWriteUnavailableError("trace_events indexes are not ready")
                result = await self.trace.write_trace_events(event_documents)
                # Metadata is repairable bookkeeping; durable event documents
                # remain authoritative if this best-effort update fails.
                upserted_ids = getattr(result, "upserted_ids", None)
                if isinstance(upserted_ids, dict):
                    inserted_documents = [
                        event_documents[index]
                        for index in upserted_ids
                        if isinstance(index, int) and 0 <= index < len(event_documents)
                    ]
                else:
                    # Some lightweight Mongo fakes expose only upserted_count;
                    # in that case retain the historical all-new assumption.
                    inserted_documents = event_documents[: int(getattr(result, "upserted_count", len(event_documents)) or 0)]
                if inserted_documents:
                    grouped_counts: dict[str, int] = defaultdict(int)
                    for document in inserted_documents:
                        grouped_counts[document["trace_id"]] += 1
                    for trace_id, count in grouped_counts.items():
                        update_one = getattr(self.trace.collection, "update_one", None)
                        if update_one is not None:
                            try:
                                await update_one(
                                    {"trace_id": trace_id},
                                    {"$inc": {"event_count": count}, "$set": {"updated_at": now}},
                                )
                            except Exception:
                                logger.warning("trace metadata repair deferred for %s", trace_id)
            except Exception:
                async with self._mongo_lock:
                    self._mongo_buffer = batch + self._mongo_buffer
                    self._flush_event.set()
                raise

            # Legacy arrays are compatibility-only in dual mode. They are
            # intentionally not written in event_store mode, avoiding $slice
            # and whole-array rewrites on the authoritative path.
            if _event_write_mode() == "dual":
                operations = await run_blocking_io(
                    _build_mongo_bulk_operations,
                    batch,
                    now=now,
                    max_events=max_events,
                    seqs_by_session=seqs_by_session,
                )
            else:
                operations = []
        else:
            operations = await run_blocking_io(
                _build_mongo_bulk_operations,
                batch,
                now=now,
                max_events=max_events,
                seqs_by_session=seqs_by_session,
            )

        # 批量执行
        if operations:
            try:
                result = await self.trace.collection.bulk_write(operations, ordered=False)
                logger.debug(
                    f"Bulk write: {result.modified_count} modified, {result.upserted_count} upserted"
                )
            except Exception as e:
                # Preserve events for a later retry and make the persistence
                # failure visible to complete()/explicit flush callers.
                async with self._mongo_lock:
                    self._mongo_buffer = batch + self._mongo_buffer
                    self._flush_event.set()
                logger.warning(f"Bulk write failed: {e}")
                raise

        # 标记完成，允许下次刷新
        self._flush_event.set()

    async def flush_mongo_buffer(self) -> None:
        """强制刷新缓冲（外部调用）"""
        flushed_by_scheduled_task = await self._drain_scheduled_flush_task()
        if not flushed_by_scheduled_task:
            await self._do_flush()

    async def _flush_redis_buffer(self) -> None:
        """保留兼容性"""
        pass

    async def complete_trace(
        self,
        trace_id: str,
        status: str = "completed",
        metadata: Optional[Dict[str, Any]] = None,
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
        if not await self.trace.ensure_indexes_if_needed():
            raise TraceWriteUnavailableError(
                "trace writes are disabled until trace indexes become ready"
            )
        if _event_write_mode() in {"dual", "event_store"}:
            return await self.trace.complete_trace(
                trace_id, status, metadata, ensure_token_usage=False
            )
        return await self.trace.complete_trace(trace_id, status, metadata)

    async def _write_to_redis_direct(
        self,
        stream_key: str,
        fields: Dict[str, str],
    ) -> bool:
        """
        单条立即写入 Redis Stream（用于流式事件，保证实时性）

        Args:
            stream_key: Redis Stream key
            fields: 已序列化的字段 dict

        Returns:
            是否写入成功
        """
        try:
            await self.redis.xadd(
                stream_key,
                fields,
            )

            now = time.monotonic()
            next_ttl_refresh_at = self._ttl_set_keys.get(stream_key)
            if next_ttl_refresh_at is None:
                ttl = await self.redis.ttl(stream_key)
                if ttl == -1:
                    await self.redis.expire(stream_key, settings.SSE_CACHE_TTL)
                self._ttl_set_keys[stream_key] = now + _get_ttl_refresh_interval()
            elif now >= next_ttl_refresh_at:
                await self.redis.expire(stream_key, settings.SSE_CACHE_TTL)
                self._ttl_set_keys[stream_key] = now + _get_ttl_refresh_interval()
            else:
                self._ttl_set_keys.move_to_end(stream_key)

            if next_ttl_refresh_at is None or now >= next_ttl_refresh_at:
                self._ttl_set_keys.move_to_end(stream_key)
                # LRU eviction
                while len(self._ttl_set_keys) > _get_ttl_set_keys_max():
                    self._ttl_set_keys.popitem(last=False)
            return True
        except Exception as e:
            logger.warning(f"Redis xadd failed (streaming event): {e}")
            return False

    async def read_from_redis(
        self,
        session_id: str,
        run_id: Optional[str] = None,
        overall_timeout: float = _LIVE_STREAM_READ_TIMEOUT_SECONDS,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        从 Redis Stream 读取事件（阻塞读取，直到流结束）

        通过定期发送 SSE 心跳注释检测客户端断开，避免僵尸连接占用资源。
        SSE 注释（以 : 开头的行）会被 EventSource 客户端自动忽略。

        Args:
            session_id: 会话 ID
            run_id: 运行 ID（用于隔离多轮对话）
            overall_timeout: 整体超时（秒），默认 24 小时，防止无限等待

        Yields:
            事件字典，包含 id, event_type, data
            心跳事件: event_type="heartbeat"（用于检测死连接）
        """
        stream_key = self._stream_key(session_id, run_id)
        last_id = "0"
        block = _REDIS_XREAD_BLOCK_MS
        heartbeat_interval = _SSE_HEARTBEAT_INTERVAL_SECONDS
        start_time = asyncio.get_event_loop().time()
        last_heartbeat = start_time
        logger.info(f"[Redis] Reading from stream: {stream_key}")

        try:
            replay_min = "-"
            replay_batch_size = _get_redis_replay_batch_size()
            replayed_count = 0
            while True:
                entries = await self.redis.xrange(
                    stream_key,
                    min=replay_min,
                    max="+",
                    count=replay_batch_size,
                )
                if not entries:
                    break
                replayed_count += len(entries)
                logger.debug(
                    "[Redis] Initial xrange replayed %d entries from %s",
                    len(entries),
                    stream_key,
                )
                for entry_id, fields in entries:
                    event = {
                        "id": entry_id,
                        "event_id": fields.get("event_id") or entry_id,
                        "event_type": fields.get("event_type"),
                        "data": await _parse_event_data_from_redis(fields.get("data", "{}")),
                        "timestamp": fields.get("timestamp"),
                    }
                    yield event
                    last_id = entry_id
                    if event["event_type"] in ("complete", "error", "done"):
                        return
                replay_min = f"({last_id}"
                if len(entries) < replay_batch_size:
                    break
            logger.info(
                f"[Redis] Initial xrange replayed {replayed_count} entries from {stream_key}"
            )

            logger.info(f"[Redis] Entering blocking xread loop for {stream_key}")
            while True:
                now = asyncio.get_event_loop().time()

                # 整体超时检查，防止 producer 崩溃导致无限等待
                elapsed = now - start_time
                if elapsed >= overall_timeout:
                    logger.warning(
                        f"[Redis] SSE read timed out after {overall_timeout}s for {stream_key}"
                    )
                    yield {
                        "id": "timeout",
                        "event_type": "error",
                        "data": {"error": "Stream read timed out"},
                        "timestamp": utc_now().isoformat(),
                    }
                    return

                # 心跳检测：定期 yield，如果客户端已断开，FastAPI 会在写入时
                # 抛出 CancelledError，从而提前释放资源
                if now - last_heartbeat >= heartbeat_interval:
                    last_heartbeat = now
                    yield {
                        "id": "heartbeat",
                        "event_type": "heartbeat",
                        "data": {},
                        "timestamp": utc_now().isoformat(),
                    }

                try:
                    results = await self.redis.xread(
                        {stream_key: last_id},
                        count=replay_batch_size,
                        block=block,
                    )
                    if results:
                        logger.debug(
                            f"[Redis] xread returned {len(results)} results from {stream_key}"
                        )
                        for _, entries in results:
                            for entry_id, fields in entries:
                                event = {
                                    "id": entry_id,
                                    "event_id": fields.get("event_id") or entry_id,
                                    "event_type": fields.get("event_type"),
                                    "data": await _parse_event_data_from_redis(
                                        fields.get("data", "{}")
                                    ),
                                    "timestamp": fields.get("timestamp"),
                                }
                                yield event
                                last_id = entry_id
                                if event["event_type"] in (
                                    "complete",
                                    "error",
                                    "done",
                                ):
                                    return
                except Exception as xread_error:
                    logger.warning(f"xread failed (non-fatal): {xread_error}")
                    await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Redis read failed: {e}")
            return

    async def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """获取完整的 trace"""
        return await self.trace.get_trace(trace_id)

    async def get_trace_events(
        self,
        trace_id: str,
        event_types: Optional[List[str]] = None,
        max_events: int = 1000,
    ) -> List[Dict[str, Any]]:
        """获取 trace 的事件列表"""
        return await self.trace.get_trace_events(trace_id, event_types, max_events=max_events)

    async def list_traces(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """列出 traces"""
        return await self.trace.list_traces(
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
            skip=skip,
        )

    async def read_session_events(
        self,
        session_id: str,
        event_types: Optional[List[str]] = None,
        run_id: Optional[str] = None,
        exclude_run_id: Optional[str] = None,
        completed_only: bool = True,
        run_ids: Optional[List[str]] = None,
        max_events: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        从 MongoDB 读取会话的所有事件（跨 traces 聚合）

        Args:
            session_id: 会话 ID
            event_types: 可选的事件类型过滤
            run_id: 可选的运行 ID 过滤（用于隔离多轮对话）
            exclude_run_id: 可选的运行 ID 排除（用于排除正在运行的 run）
            completed_only: 是否只返回完成的 trace 中的事件（默认 True）
            run_ids: 可选的运行 ID 列表过滤
            max_events: 可选的最大返回事件数

        Returns:
            事件列表
        """
        if _event_read_mode() == "event_store":
            return await self.trace.get_event_store_session_events(
                session_id,
                event_types=event_types,
                run_id=run_id,
                exclude_run_id=exclude_run_id,
                completed_only=completed_only,
                run_ids=run_ids,
            )
        if _event_read_mode() == "merge":
            probe = getattr(self.trace, "get_history_ordering_version", None)
            ordering_version = (
                await probe(
                    session_id,
                    run_id=run_id,
                    exclude_run_id=exclude_run_id,
                    completed_only=completed_only,
                    run_ids=run_ids,
                )
                if probe is not None
                else HISTORY_ORDERING_VERSION
            )
            immutable = await self.trace.get_event_store_session_events(
                session_id, event_types=event_types, run_id=run_id,
                exclude_run_id=exclude_run_id, completed_only=completed_only,
                run_ids=run_ids,
            )
            legacy = await self.trace.get_session_events(
                session_id, event_types, run_id=run_id, exclude_run_id=exclude_run_id,
                completed_only=completed_only, run_ids=run_ids, max_events=max_events,
                # Preserve each trace-array ordinal so the fallback identity
                # exactly matches ``backfill_legacy_events``.  Using the
                # flattened session ordinal would make a backfilled legacy
                # event look like a second event during merge reads.
                _include_cursor_metadata=True,
                _ordering_version=ordering_version,
            )
            merged: dict[str, Dict[str, Any]] = {}
            for flattened_ordinal, event in enumerate(legacy):
                # Legacy arrays written before the shared ``event_id`` field
                # was introduced still need a deterministic identity that
                # matches backfill-generated immutable documents.
                trace_ordinal = event.get("_event_index")
                if not isinstance(trace_ordinal, int) or trace_ordinal < 0:
                    trace_ordinal = flattened_ordinal
                key = str(
                    event.get("event_id")
                    or event.get("id")
                    or TraceStorage.legacy_event_id(
                        str(event.get("trace_id") or ""), trace_ordinal, event
                    )
                )
                event.setdefault("event_id", key)
                if ordering_version == HISTORY_COMPAT_ORDERING_VERSION and "history_order" not in event:
                    event["history_order"] = history_ordering_key(event, ordinal=trace_ordinal)
                event.pop("_event_index", None)
                merged[key] = event
            for ordinal, event in enumerate(immutable):
                key = str(event.get("event_id") or event.get("id") or "")
                if key:
                    if ordering_version == HISTORY_COMPAT_ORDERING_VERSION:
                        event["history_order"] = history_ordering_key(event, ordinal=ordinal)
                    merged[key] = event
            ordering_key = history_ordering_key if ordering_version == HISTORY_COMPAT_ORDERING_VERSION else event_ordering_key
            return sorted(merged.values(), key=ordering_key)
        return await self.trace.get_session_events(
            session_id,
            event_types,
            run_id=run_id,
            exclude_run_id=exclude_run_id,
            completed_only=completed_only,
            run_ids=run_ids,
            max_events=max_events,
        )

    async def read_session_events_page(
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
        """Read a cursor page from the legacy trace store."""
        if _event_read_mode() == "event_store":
            return await self.trace.get_event_store_session_events_page(
                session_id, event_types=event_types, run_id=run_id,
                exclude_run_id=exclude_run_id, completed_only=completed_only,
                run_ids=run_ids, limit=limit, after=after,
            )
        if _event_read_mode() == "merge":
            probe = getattr(self.trace, "get_history_ordering_version", None)
            ordering_version = (
                await probe(
                    session_id,
                    run_id=run_id,
                    exclude_run_id=exclude_run_id,
                    completed_only=completed_only,
                    run_ids=run_ids,
                )
                if probe is not None
                else HISTORY_ORDERING_VERSION
            )
            default_limit = getattr(settings, "SESSION_EVENT_READ_DEFAULT_LIMIT", 1000)
            page_limit = max(min(int(str(limit or default_limit)), 10000), 1)
            # Merge mode applies the cursor after combining the two sources in
            # memory. Once a continuation cursor is present, reading only
            # ``page_limit + 1`` rows would truncate the prefix before the
            # cursor and make later pages appear empty. Read the bounded
            # retained history for continuation requests, then apply the
            # exclusive key below.
            read_limit = (
                TRACE_EVENTS_READ_LIMIT
                if after
                else min(page_limit + 1, TRACE_EVENTS_READ_LIMIT)
            )
            events = await self.read_session_events(
                session_id, event_types, run_id=run_id, exclude_run_id=exclude_run_id,
                completed_only=completed_only, run_ids=run_ids,
                # The merge reader must retain a probe row; otherwise a
                # legacy-only session with more than the default 1000 events
                # would report ``has_more=false`` and make the rest
                # unreachable.  Event-store mode has its own unbounded page
                # reader; this bound is only the migration fallback.
                max_events=read_limit,
            )
            fingerprint = filter_fingerprint(
                scope=session_id, event_types=event_types or [], run_id=run_id,
                exclude_run_id=exclude_run_id, run_ids=run_ids or [],
            )
            if after:
                key = decode_history_cursor(
                    after,
                    scope=session_id,
                    fingerprint=fingerprint,
                    ordering_version=ordering_version,
                )
                ordering_key = history_ordering_key if ordering_version == HISTORY_COMPAT_ORDERING_VERSION else event_ordering_key
                events = [event for event in events if ordering_key(event) > key]
            has_more = len(events) > page_limit
            page_events = events[:page_limit]
            ordering_key = history_ordering_key if ordering_version == HISTORY_COMPAT_ORDERING_VERSION else event_ordering_key
            next_cursor = encode_history_cursor(
                scope=session_id,
                fingerprint=fingerprint,
                key=ordering_key(page_events[-1]),
                ordering_version=ordering_version,
            ) if has_more and page_events else None
            return {
                "events": page_events, "has_more": has_more, "next_cursor": next_cursor,
                "history_complete": False, "ordering_version": ordering_version,
                "events_limited": has_more, "events_limit": limit,
            }
        return await self.trace.get_session_events_page(
            session_id,
            event_types,
            run_id=run_id,
            exclude_run_id=exclude_run_id,
            completed_only=completed_only,
            run_ids=run_ids,
            limit=limit,
            after=after,
        )

    async def get_stream_length(self, session_id: str, run_id: Optional[str] = None) -> int:
        """
        获取 Redis Stream 长度

        Args:
            session_id: 会话 ID
            run_id: 运行 ID（可选）
        """
        stream_key = self._stream_key(session_id, run_id)
        try:
            return await self.redis.xlen(stream_key)
        except Exception:
            return 0

    async def clear_stream(self, session_id: str, run_id: Optional[str] = None) -> None:
        """
        清除 Redis Stream

        Args:
            session_id: 会话 ID
            run_id: 运行 ID（可选）
        """
        stream_key = self._stream_key(session_id, run_id)
        try:
            await self.redis.delete(stream_key)
        except Exception as e:
            logger.warning(f"Failed to clear stream: {e}")

    async def expire_stream(
        self,
        session_id: str,
        run_id: Optional[str] = None,
        ttl_seconds: int = 60,
    ) -> bool:
        """
        Shorten Redis Stream TTL after a run reaches a terminal state.

        Keeping a short grace period avoids racing active SSE readers that still
        need the terminal event, while preventing completed runs from occupying
        Redis for the full live-stream TTL.
        """
        stream_key = self._stream_key(session_id, run_id)
        try:
            ttl = max(int(ttl_seconds), 1)
            success = await self.redis.expire(stream_key, ttl)
            self._ttl_set_keys.pop(stream_key, None)
            return bool(success)
        except Exception as e:
            logger.warning(f"Failed to expire stream: {e}")
            return False


# Singleton instance
_dual_writer: Optional[DualEventWriter] = None


def get_dual_writer() -> DualEventWriter:
    """获取 DualEventWriter 单例"""
    global _dual_writer
    if _dual_writer is None:
        _dual_writer = DualEventWriter()
    return _dual_writer
