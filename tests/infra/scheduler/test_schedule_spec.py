from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.infra.scheduler.schedule_spec import (
    ScheduleValidationError,
    build_trigger,
    describe,
    next_fire_times,
    validate_spec,
)
from src.kernel.schemas.scheduled_task import ScheduleSpec

TZ = "Asia/Shanghai"
# Wednesday 2026-09-30 09:00 Beijing time
NOW = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)


def _spec(**kw) -> ScheduleSpec:
    return ScheduleSpec(**kw)


def test_weekdays_skips_weekend_in_beijing_time() -> None:
    runs = next_fire_times(_spec(kind="weekdays", time="08:30"), TZ, 4, now=NOW)
    assert [r.strftime("%m-%d %H:%M") for r in runs] == [
        "10-01 08:30",
        "10-02 08:30",
        "10-05 08:30",
        "10-06 08:30",
    ]
    assert all(r.utcoffset() == timedelta(hours=8) for r in runs)


def test_daily_today_if_not_passed() -> None:
    runs = next_fire_times(_spec(kind="daily", time="18:00"), TZ, 2, now=NOW)
    assert runs[0].strftime("%m-%d %H:%M") == "09-30 18:00"


def test_weekly_multiple_days_and_description() -> None:
    spec = _spec(kind="weekly", time="12:30", weekdays=[5, 1, 3, 3])
    assert spec.weekdays == [1, 3, 5]
    assert describe(spec) == "每周周一、周三、周五 12:30"
    runs = next_fire_times(spec, TZ, 3, now=NOW)
    assert [r.isoweekday() for r in runs] == [3, 5, 1]


def test_monthly_last_day() -> None:
    spec = _spec(kind="monthly", time="15:00", month_day=-1)
    runs = next_fire_times(spec, TZ, 3, now=NOW)
    assert [r.day for r in runs] == [30, 31, 30]
    assert describe(spec) == "每月最后一天 15:00"


def test_once_naive_datetime_is_task_timezone() -> None:
    spec = _spec(kind="once", run_at=datetime(2026, 10, 8, 10, 0))
    trigger = build_trigger(spec, TZ)
    assert isinstance(trigger, DateTrigger)
    runs = next_fire_times(spec, TZ, 3, now=NOW)
    assert len(runs) == 1 and runs[0].hour == 10


def test_interval_anchor_is_deterministic() -> None:
    anchor = datetime.now(timezone.utc) - timedelta(minutes=30)
    spec = _spec(kind="interval", every_hours=2)
    a = next_fire_times(spec, TZ, 1, anchor=anchor)[0]
    b = next_fire_times(spec, TZ, 1, anchor=anchor)[0]
    assert a == b
    assert abs((a - (anchor + timedelta(hours=2))).total_seconds()) < 1
    assert isinstance(build_trigger(spec, TZ, anchor=anchor), IntervalTrigger)


def test_cron_kinds_build_cron_triggers() -> None:
    assert isinstance(build_trigger(_spec(kind="daily", time="9:05"), TZ), CronTrigger)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kind": "daily", "time": "25:00"}, "执行时间"),
        ({"kind": "daily"}, "执行时间"),
        ({"kind": "weekly", "time": "09:00", "weekdays": []}, "至少选择一天"),
        ({"kind": "monthly", "time": "09:00", "month_day": 0}, "每月的第几天"),
        ({"kind": "once", "run_at": datetime(2020, 1, 1, 9, 0)}, "已经过去"),
        ({"kind": "interval", "every_hours": 30}, "1 到 24"),
    ],
)
def test_validation_messages_are_friendly(kwargs, message) -> None:
    with pytest.raises(ScheduleValidationError, match=message):
        validate_spec(_spec(**kwargs), TZ, now=NOW)


def test_min_interval_enforced() -> None:
    with pytest.raises(ScheduleValidationError, match="不能少于 4 小时"):
        validate_spec(_spec(kind="interval", every_hours=2), TZ, min_interval_hours=4)


def test_bad_timezone() -> None:
    with pytest.raises(ScheduleValidationError, match="时区"):
        validate_spec(_spec(kind="daily", time="09:00"), "Mars/Base")
