"""接线测试：证明预聚合写侧真的被产品链路调用，而不只是 API 正确。

上一版的教训：11 条单元测试全绿，但 ``accumulate`` 在 ``src/`` 里零消费者，
功能整条没接通。这里的断言针对"链路"本身。
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.infra.analytics import usage_hourly_recorder as recorder_module
from src.infra.analytics.usage_hourly_recorder import UsageHourlyRecorder

REPO_ROOT = Path(__file__).resolve().parents[3]


def _module_calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def test_token_usage_events_feed_the_recorder() -> None:
    """``token:usage`` 落库处必须调用 recorder，否则预聚合永远没有 live 行。"""
    calls = _module_calls(REPO_ROOT / "src/infra/writer/presenter_storage.py")
    assert "_record_usage_hourly" in calls
    assert "record_token_usage" in calls


def test_user_message_events_feed_the_recorder() -> None:
    """``user:message`` 落库处必须调用 recorder，否则消息维度永远没有 live 行。"""
    calls = _module_calls(REPO_ROOT / "src/infra/writer/presenter_storage.py")
    assert "_record_usage_hourly_message" in calls
    assert "record_user_message" in calls


def test_message_rows_carry_no_model_dimension() -> None:
    """消息行的 model 必须是 ``None``（被按模型聚合过滤）；字符串 ``unknown``
    会穿透 ``$ne: None``，在模型图表里产生零值幽灵图例项。"""
    source = (
        REPO_ROOT / "src/infra/analytics/usage_hourly_recorder.py"
    ).read_text(encoding="utf-8")
    record_block = source.split("async def record_user_message", 1)[1].split(
        "async def ", 1
    )[0]
    assert '"unknown"' not in record_block
    assert "\n            None," in record_block


def test_workers_and_indexes_are_mounted_in_lifespan() -> None:
    """worker 与索引创建必须挂在 lifespan，否则只能手工跑。"""
    main_source = (REPO_ROOT / "src/api/main.py").read_text(encoding="utf-8")
    assert "usage_hourly_flush_task" in main_source
    assert "usage_hourly_backfill_task" in main_source
    assert "ensure_indexes" in main_source
    # 必须同时登记任务名，否则关停时不会被取消。
    assert main_source.count("usage_hourly_flush_task") >= 2
    assert main_source.count("usage_hourly_backfill_task") >= 2


def test_flush_setting_drives_the_flush_loop_not_the_backfill() -> None:
    """FLUSH_SECONDS 必须驱动刷写；回填有自己的间隔设置。"""
    backfill = (REPO_ROOT / "src/infra/analytics/usage_hourly_backfill.py").read_text(
        encoding="utf-8"
    )
    assert "ANALYTICS_USAGE_HOURLY_BACKFILL_INTERVAL_SECONDS" in backfill
    assert "ANALYTICS_USAGE_HOURLY_FLUSH_SECONDS" not in backfill

    rec = (REPO_ROOT / "src/infra/analytics/usage_hourly_recorder.py").read_text(
        encoding="utf-8"
    )
    assert "ANALYTICS_USAGE_HOURLY_FLUSH_SECONDS" in rec


class _FakeStorage:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def accumulate_many(self, rows: list[dict[str, Any]], **_: Any) -> list[Any]:
        self.rows.extend(rows)
        return []


def test_recorder_buffers_then_flushes_live_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.kernel.config import settings

    monkeypatch.setattr(settings, "ANALYTICS_USAGE_HOURLY_ENABLED", True, raising=False)
    storage = _FakeStorage()
    rec = UsageHourlyRecorder(storage=storage)  # type: ignore[arg-type]
    at = datetime(2026, 9, 20, 10, 30, tzinfo=timezone.utc)

    async def scenario() -> None:
        await rec.record_token_usage(
            data={"total_tokens": 100, "model": "gpt-4o"}, user_id="u1", occurred_at=at
        )
        await rec.record_token_usage(
            data={"total_tokens": 50, "model": "gpt-4o"}, user_id="u1", occurred_at=at
        )
        # 未 flush 前不落库
        assert storage.rows == []
        written = await rec.flush()
        assert written == 1

    asyncio.run(scenario())
    assert len(storage.rows) == 1
    row = storage.rows[0]
    assert row["source"] == "live"
    assert row["model"] == "gpt-4o"
    assert row["tokens"] == 150  # 同桶累加
    assert row["bucket"] == datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def test_recorder_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.kernel.config import settings

    monkeypatch.setattr(settings, "ANALYTICS_USAGE_HOURLY_ENABLED", False, raising=False)
    storage = _FakeStorage()
    rec = UsageHourlyRecorder(storage=storage)  # type: ignore[arg-type]

    async def scenario() -> None:
        await rec.record_token_usage(data={"total_tokens": 10}, user_id="u1")
        assert await rec.flush() == 0

    asyncio.run(scenario())
    assert storage.rows == []


def test_failed_flush_requeues_instead_of_losing_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.kernel.config import settings

    monkeypatch.setattr(settings, "ANALYTICS_USAGE_HOURLY_ENABLED", True, raising=False)

    class _Failing(_FakeStorage):
        async def accumulate_many(self, rows: list[dict[str, Any]], **_: Any) -> list[Any]:
            raise RuntimeError("mongo down")

    rec = UsageHourlyRecorder(storage=_Failing())  # type: ignore[arg-type]

    async def scenario() -> None:
        await rec.record_token_usage(data={"total_tokens": 7}, user_id="u1")
        assert await rec.flush() == 0
        assert rec.pending_keys == 1  # 未丢失，下轮重试

    asyncio.run(scenario())


def test_model_label_matches_analytics_convention() -> None:
    from src.infra.analytics.storage import _model_label

    for data in (
        {"model_id": "cfg-1", "model": "gpt-4o"},
        {"model": "claude"},
        {},
    ):
        assert recorder_module.resolve_model_label(data) == _model_label(data)


def test_recorder_singleton_is_reusable() -> None:
    recorder_module.reset_usage_hourly_recorder()
    first = recorder_module.get_usage_hourly_recorder()
    assert recorder_module.get_usage_hourly_recorder() is first
    recorder_module.reset_usage_hourly_recorder()
