"""CheckpointCleanupWorker 无外部 Mongo/Redis 依赖的测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.infra.checkpoint.cleanup_worker import (
    CHECKPOINT_CLEANUP_LOCK_KEY,
    CheckpointCleanupWorker,
)

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


class _Redis:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.set_calls: list[tuple] = []
        self.eval_calls: list[tuple] = []

    async def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))
        return self.acquired

    async def eval(self, *args, **kwargs):
        self.eval_calls.append((args, kwargs))
        return 1

    async def aclose(self):
        return None


class _SessionStorage:
    def __init__(self, session_ids: list[str]) -> None:
        self.session_ids = session_ids
        self.calls: list[dict] = []

    async def list_inactive_session_ids(self, *, cutoff, limit, exclude_ids=None):
        self.calls.append({"cutoff": cutoff, "limit": limit})
        return list(self.session_ids)


class _ApprovalStorage:
    def __init__(self, pending_sessions: set[str] | None = None) -> None:
        self.pending_sessions = pending_sessions or set()

    async def list_pending(self, session_id=None, user_id=None, limit=100):
        return ["approval"] if session_id in self.pending_sessions else []


def _build_worker(monkeypatch, **kwargs):
    monkeypatch.setattr(
        "src.infra.checkpoint.cleanup_worker.is_checkpoint_backend_enabled",
        lambda: kwargs.pop("backend_enabled", True),
    )
    deleted: list[str] = []

    async def _delete(thread_id: str) -> None:
        deleted.append(thread_id)

    worker = CheckpointCleanupWorker(
        redis_client=kwargs.pop("redis", _Redis()),
        session_storage=kwargs.pop("session_storage", _SessionStorage([])),
        approval_storage=kwargs.pop("approval_storage", _ApprovalStorage()),
        retention_days=kwargs.pop("retention_days", 30),
        batch_limit=kwargs.pop("batch_limit", 200),
        now_factory=lambda: NOW,
        delete_thread=_delete,
    )
    return worker, deleted


@pytest.fixture(autouse=True)
def _enable_cleanup(monkeypatch):
    monkeypatch.setattr(
        "src.infra.checkpoint.cleanup_worker.settings.CHECKPOINT_CLEANUP_ENABLED",
        True,
        raising=False,
    )


@pytest.mark.asyncio
async def test_cleanup_deletes_inactive_sessions_and_releases_lock(monkeypatch) -> None:
    redis = _Redis()
    storage = _SessionStorage(["s1", "s2"])
    worker, deleted = _build_worker(monkeypatch, redis=redis, session_storage=storage)

    assert await worker.run_once() == 2
    assert deleted == ["s1", "s2"]
    # cutoff is retention_days before now, so only long-inactive sessions match
    assert storage.calls[0]["cutoff"] == NOW - timedelta(days=30)
    assert redis.eval_calls  # lock released via CAS
    assert redis.set_calls[0][1]["nx"] is True


@pytest.mark.asyncio
async def test_cleanup_lock_contention_is_a_noop(monkeypatch) -> None:
    redis = _Redis(acquired=False)
    worker, deleted = _build_worker(
        monkeypatch, redis=redis, session_storage=_SessionStorage(["s1"])
    )

    assert await worker.run_once() == 0
    assert deleted == []
    assert redis.eval_calls[0][0][2] == CHECKPOINT_CLEANUP_LOCK_KEY


@pytest.mark.asyncio
async def test_cleanup_skips_sessions_with_pending_approval(monkeypatch) -> None:
    worker, deleted = _build_worker(
        monkeypatch,
        session_storage=_SessionStorage(["s1", "s2"]),
        approval_storage=_ApprovalStorage({"s1"}),
    )

    assert await worker.run_once() == 1
    assert deleted == ["s2"]


@pytest.mark.asyncio
async def test_cleanup_disabled_does_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.infra.checkpoint.cleanup_worker.settings.CHECKPOINT_CLEANUP_ENABLED",
        False,
        raising=False,
    )
    worker, deleted = _build_worker(monkeypatch, session_storage=_SessionStorage(["s1"]))

    assert await worker.run_once() == 0
    assert deleted == []


@pytest.mark.asyncio
async def test_cleanup_noop_when_checkpoint_backend_disabled(monkeypatch) -> None:
    worker, deleted = _build_worker(
        monkeypatch,
        session_storage=_SessionStorage(["s1"]),
        backend_enabled=False,
    )

    assert await worker.run_once() == 0
    assert deleted == []


@pytest.mark.asyncio
async def test_retention_floor_and_batch_bounds_are_enforced(monkeypatch) -> None:
    storage = _SessionStorage([])
    worker, _ = _build_worker(
        monkeypatch,
        session_storage=storage,
        retention_days=1,
        batch_limit=100_000,
    )

    await worker.run_once()
    # 1 day is clamped up to the 7-day floor; batch limit is clamped down
    assert storage.calls[0]["cutoff"] == NOW - timedelta(days=7)
    assert storage.calls[0]["limit"] == 1000


@pytest.mark.asyncio
async def test_delete_failure_does_not_abort_batch(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.infra.checkpoint.cleanup_worker.is_checkpoint_backend_enabled",
        lambda: True,
    )
    deleted: list[str] = []

    async def _delete(thread_id: str) -> None:
        if thread_id == "s1":
            raise RuntimeError("mongo down")
        deleted.append(thread_id)

    worker = CheckpointCleanupWorker(
        redis_client=_Redis(),
        session_storage=_SessionStorage(["s1", "s2"]),
        approval_storage=_ApprovalStorage(),
        now_factory=lambda: NOW,
        delete_thread=_delete,
    )

    assert await worker.run_once() == 1
    assert deleted == ["s2"]
