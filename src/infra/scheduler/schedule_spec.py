"""Compile friendly ``ScheduleSpec`` into APScheduler triggers.

Also produces a Chinese human-readable description and a next-N preview so the
UI never exposes cron syntax. All user-facing validation errors are raised as
``ScheduleValidationError`` with a Chinese message that can be shown verbatim.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.infra.utils.datetime import ensure_utc, utc_now
from src.kernel.schemas.scheduled_task import ScheduleKind, ScheduleSpec

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_CRON_DOW = {1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat", 7: "sun"}
_ZH_DOW = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}


class ScheduleValidationError(ValueError):
    """User-facing (Chinese) schedule validation error."""


def resolve_timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((name or "Asia/Shanghai").strip() or "Asia/Shanghai")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleValidationError(f"无法识别的时区：{name}") from exc


def _parse_time(spec: ScheduleSpec) -> tuple[int, int]:
    raw = (spec.time or "").strip()
    match = _TIME_RE.match(raw)
    if not match:
        raise ScheduleValidationError("请选择执行时间（例如 09:00）")
    return int(match.group(1)), int(match.group(2))


def _local_run_at(spec: ScheduleSpec, tz: ZoneInfo) -> datetime:
    if spec.run_at is None:
        raise ScheduleValidationError("请选择执行的日期和时间")
    run_at = spec.run_at
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=tz)
    return run_at


def validate_spec(
    spec: ScheduleSpec,
    timezone_name: str | None,
    *,
    min_interval_hours: int = 1,
    now: datetime | None = None,
) -> None:
    """Validate a spec; raise ``ScheduleValidationError`` with a friendly message."""
    tz = resolve_timezone(timezone_name)
    kind = spec.kind
    if kind in (
        ScheduleKind.DAILY,
        ScheduleKind.WEEKDAYS,
        ScheduleKind.WEEKLY,
        ScheduleKind.MONTHLY,
    ):
        _parse_time(spec)
    if kind == ScheduleKind.WEEKLY:
        days = spec.weekdays or []
        if not days:
            raise ScheduleValidationError("请至少选择一天（周一到周日）")
        if any(d < 1 or d > 7 for d in days):
            raise ScheduleValidationError("星期只能在周一到周日之间")
    if kind == ScheduleKind.MONTHLY:
        day = spec.month_day
        if day is None or not (day == -1 or 1 <= day <= 31):
            raise ScheduleValidationError("请选择每月的第几天（1-31 号，或最后一天）")
    if kind == ScheduleKind.ONCE:
        run_at = _local_run_at(spec, tz)
        if ensure_utc(run_at) <= (now or utc_now()) + timedelta(seconds=30):
            raise ScheduleValidationError("执行时间已经过去了，请选择一个将来的时间")
    if kind == ScheduleKind.INTERVAL:
        hours = spec.every_hours
        if hours is None or hours < 1 or hours > 24:
            raise ScheduleValidationError("间隔只能设置为 1 到 24 小时")
        if hours < max(1, min_interval_hours):
            raise ScheduleValidationError(f"为节约系统资源，间隔不能少于 {min_interval_hours} 小时")


def build_trigger(
    spec: ScheduleSpec,
    timezone_name: str | None,
    *,
    anchor: datetime | None = None,
) -> BaseTrigger:
    """Build an APScheduler trigger.

    ``anchor`` (last run or creation time) pins interval triggers so every
    replica computes identical fire times (LambChat multi-instance idea).
    """
    tz = resolve_timezone(timezone_name)
    kind = spec.kind
    if kind == ScheduleKind.ONCE:
        return DateTrigger(run_date=ensure_utc(_local_run_at(spec, tz)), timezone="UTC")
    if kind == ScheduleKind.INTERVAL:
        hours = int(spec.every_hours or 1)
        start = None
        if anchor is not None:
            start = ensure_utc(anchor) + timedelta(hours=hours)
            # Never schedule in the past; skip missed slots deterministically.
            now = utc_now()
            if start < now:
                missed = int((now - start).total_seconds() // (hours * 3600)) + 1
                start = start + timedelta(hours=hours * missed)
        return IntervalTrigger(hours=hours, start_date=start, timezone=tz)
    hour, minute = _parse_time(spec)
    if kind == ScheduleKind.DAILY:
        return CronTrigger(hour=hour, minute=minute, second=0, timezone=tz)
    if kind == ScheduleKind.WEEKDAYS:
        return CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, second=0, timezone=tz)
    if kind == ScheduleKind.WEEKLY:
        dow = ",".join(_CRON_DOW[d] for d in (spec.weekdays or []))
        return CronTrigger(day_of_week=dow, hour=hour, minute=minute, second=0, timezone=tz)
    if kind == ScheduleKind.MONTHLY:
        day = "last" if spec.month_day == -1 else str(spec.month_day)
        return CronTrigger(day=day, hour=hour, minute=minute, second=0, timezone=tz)
    raise ScheduleValidationError("不支持的执行频率")


def describe(spec: ScheduleSpec, timezone_name: str | None = None) -> str:
    """Chinese human-readable description, e.g. ``每个工作日 08:30``."""
    kind = spec.kind
    t = (spec.time or "").strip()
    if kind == ScheduleKind.DAILY:
        return f"每天 {t}"
    if kind == ScheduleKind.WEEKDAYS:
        return f"每个工作日（周一至周五）{t}"
    if kind == ScheduleKind.WEEKLY:
        days = spec.weekdays or []
        if days == [1, 2, 3, 4, 5, 6, 7]:
            return f"每天 {t}"
        return "每周" + "、".join(f"周{_ZH_DOW.get(d, '?')}" for d in days) + f" {t}"
    if kind == ScheduleKind.MONTHLY:
        if spec.month_day == -1:
            return f"每月最后一天 {t}"
        return f"每月 {spec.month_day} 号 {t}"
    if kind == ScheduleKind.ONCE:
        if spec.run_at is None:
            return "仅一次"
        tz = resolve_timezone(timezone_name)
        local = _local_run_at(spec, tz).astimezone(tz)
        return f"仅一次：{local:%Y-%m-%d %H:%M}"
    if kind == ScheduleKind.INTERVAL:
        return f"每隔 {spec.every_hours} 小时"
    return ""


def next_fire_times(
    spec: ScheduleSpec,
    timezone_name: str | None,
    count: int = 5,
    *,
    anchor: datetime | None = None,
    now: datetime | None = None,
) -> list[datetime]:
    """Compute the next ``count`` fire times (timezone-aware, task timezone)."""
    tz = resolve_timezone(timezone_name)
    trigger = build_trigger(spec, timezone_name, anchor=anchor)
    current = (now or utc_now()).astimezone(tz)
    results: list[datetime] = []
    previous: datetime | None = None
    for _ in range(max(1, count)):
        fire = trigger.get_next_fire_time(previous, current)
        if fire is None:
            break
        results.append(fire.astimezone(tz))
        previous = fire
        current = fire + timedelta(seconds=1)
    return results
