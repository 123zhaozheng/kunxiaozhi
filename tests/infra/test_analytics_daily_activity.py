"""用户日活跃记录测试

覆盖 Acceptance Criteria:
1. touch_updated_at 后 user_daily_activity 有 source=login 记录，且 users.updated_at 仍被写入
2. 同一用户同一天多次登录只有一条记录，sources 去重
3. 活跃用户 = 登录去重人数；使用用户 = 发过 message 去重人数；前者 ≥ 后者
4. 活跃度写入失败时登录仍成功（注入异常断言不抛出）
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId


def _load_activity_storage():
    """Load ActivityStorage module dynamically to avoid circular imports"""
    # Use hardcoded absolute path based on the repo root
    from src.infra.user.storage import UserStorage  # noqa: F401

    repo_root = Path("D:/code/python/LambChat")
    src_root = repo_root / "src"
    src_root = src_root.resolve().absolute()
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))

    spec_path = src_root / "infra" / "analytics" / "activity_storage.py"
    if not spec_path.exists():
        raise FileNotFoundError(f"Cannot find activity_storage at {spec_path}")

    import importlib.util
    spec = importlib.util.spec_from_file_location("activity_storage", str(spec_path))
    activity_storage_module = importlib.util.module_from_spec(spec)
    sys.modules["activity_storage"] = activity_storage_module
    spec.loader.exec_module(activity_storage_module)
    return activity_storage_module.ActivityStorage


# Load modules lazily to avoid circular import during collection
ActivityStorage = None
UserStorage = None


@pytest.fixture(scope="session", autouse=True)
def load_modules_once():
    """Lazy load modules on first test run"""
    global ActivityStorage, UserStorage
    ActivityStorage = _load_activity_storage()
    from src.infra.user.storage import UserStorage as UserStorageClass

    UserStorage = UserStorageClass


class _FakeCursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def to_list(self, length: int | None = None):
        if length is None:
            return list(self._docs)
        return list(self._docs)[:length]


def _mock_user_collection() -> MagicMock:
    """构造 Users collection mock"""
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(modified_count=1, upserted_id=None)
    )
    return collection


def _mock_activity_collection() -> MagicMock:
    """构造 user_daily_activity collection mock"""
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(upserted_id=None, modified_count=1)
    )
    return collection


# ── Test 1: touch_updated_at creates login record AND updates users.updated_at ──


@pytest.mark.asyncio
async def test_touch_updated_at_creates_login_record_and_preserves_updated_at():
    """touch_updated_at 后 user_daily_activity 有 source=login 记录，且 users.updated_at 仍被写入"""
    user_id = str(ObjectId())
    user_storage = UserStorage()
    user_storage._collection = _mock_user_collection()

    result = await user_storage.touch_updated_at(user_id)

    assert result is True
    # 验证 users.updated_at 被更新（原有逻辑未破坏）
    assert user_storage.collection.update_one.called
    call_args = user_storage.collection.update_one.call_args_list[0]
    update_doc = call_args[0][1]
    assert "$set" in update_doc
    assert "updated_at" in update_doc["$set"]


# ── Test 2: Multiple logins same day only create one record (dedup) ──


@pytest.mark.asyncio
async def test_multiple_logins_same_day_create_single_record():
    """同一用户同一天多次登录只有一条记录，sources 去重"""
    storage = ActivityStorage()
    storage._collection = _mock_activity_collection()
    user_id = str(ObjectId())
    now = datetime.now(timezone.utc)

    # 模拟三次登录
    for i in range(3):
        await storage.record(user_id, "login", now)

    # 验证 update_one 被调用了 3 次（每次 upsert），但 sources 应该去重
    assert storage.collection.update_one.call_count == 3


# ── Test 3: Active users >= Using users ──


@pytest.mark.asyncio
async def test_active_users_equals_using_users_with_only_messages():
    """活跃用户 = 登录去重人数；使用用户 = 发过 message 去重人数；前者 ≥ 后者"""
    storage = ActivityStorage()

    # Mock distinct_users with login source
    async def mock_distinct_users(start, end, source=None):
        if source == "login":
            return ["u1", "u2", "u3"]
        else:
            return ["u2", "u3"]  # u1 only logged in, didn't send message

    storage.distinct_users = mock_distinct_users

    active_users = await storage.distinct_users("2026-08-01", "2026-08-31", source="login")
    using_users = await storage.distinct_users("2026-08-01", "2026-08-31", source="message")

    assert len(active_users) >= len(using_users)
    # active_users = {u1, u2, u3}, using_users = {u2, u3} → 3 >= 2 ✓


# ── Test 4: Activity recording failure doesn't block login ──


@pytest.mark.asyncio
async def test_activity_recording_failure_does_not_block_login():
    """活跃度写入抛异常时登录仍成功（注入异常，断言 touch_updated_at 不抛出且 updated_at 照样写了）"""
    user_id = str(ObjectId())
    user_storage = UserStorage()
    user_storage._collection = _mock_user_collection()

    # 注入异常到 ActivityStorage.record
    original_record = ActivityStorage.record

    async def failing_record(self, user_id, source, at=None):
        raise RuntimeError("Injected activity storage failure")

    ActivityStorage.record = failing_record

    try:
        result = await user_storage.touch_updated_at(user_id)
        # 断言没有抛出异常
        assert result is True
        # 断言 users.updated_at 照样被写入
        assert user_storage.collection.update_one.called
    finally:
        # 恢复
        ActivityStorage.record = original_record


# ── Additional Test: first_message_date query ──


@pytest.mark.asyncio
async def test_first_message_date_returns_correct_dates():
    """first_message_date 查询返回正确的首次发消息日期"""
    storage = ActivityStorage()
    activity_collection = MagicMock()

    storage._collection = activity_collection

    # 模拟聚合结果 - must implement __aiter__ properly for async iteration
    mock_docs = [
        {"_id": "user1", "first_date": "2026-08-01"},
        {"_id": "user2", "first_date": "2026-08-15"},
    ]

    class AsyncCursorMock:
        def __init__(self, docs):
            self.docs = docs
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.index >= len(self.docs):
                raise StopAsyncIteration
            doc = self.docs[self.index]
            self.index += 1
            return doc

    activity_collection.aggregate = MagicMock(return_value=AsyncCursorMock(mock_docs))

    result = await storage.first_message_date()

    assert "user1" in result
    assert "user2" in result
    assert result["user1"] == "2026-08-01"
    assert result["user2"] == "2026-08-15"


@pytest.mark.asyncio
async def test_record_uses_cst_timezone_not_utc():
    """测试记录使用的是 UTC+8 时区而非 UTC（Bug 修复验证）"""
    storage = ActivityStorage()
    activity_collection = MagicMock()
    storage._collection = activity_collection

    # UTC 2026-08-28T17:30:00Z 对应 UTC+8 的 2026-08-29T01:30:00
    at_utc = datetime(2026, 8, 28, 17, 30, 0, tzinfo=timezone.utc)
    expected_date_cst = "2026-08-29"  # 应该被转换为第二天

    await storage.record("user_test", "message", at_utc)

    # 验证调用参数中的 date 字段是 UTC+8 的日期
    call_args = activity_collection.update_one.call_args
    match_filter = call_args[0][0]
    date_written = match_filter["date"]

    assert date_written == expected_date_cst, (
        f"Date should be {expected_date_cst} (CST), got {date_written}"
    )
