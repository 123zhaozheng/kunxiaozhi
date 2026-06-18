"""Unified process-local scheduler built on APScheduler."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.infra.logging import get_logger
from src.infra.utils.datetime import utc_now

logger = get_logger(__name__)

IntervalValue = int | Callable[[], int]
EnabledValue = bool | Callable[[], bool]
JobHandler = Callable[[], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """A managed interval task."""

    id: str
    interval_seconds: IntervalValue
    handler: JobHandler
    enabled: EnabledValue = True
    name: str | None = None
    max_instances: int = 1
    coalesce: bool = True
    run_on_start: bool = False


class RuntimeScheduler:
    """Small APScheduler facade for 昆小智 runtime services."""

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None
        self._jobs: dict[str, ScheduledJob] = {}
        self._scheduled_intervals: dict[str, int] = {}

    def register_interval_job(self, job: ScheduledJob) -> None:
        """Register or replace an interval job."""
        if not job.id:
            raise ValueError("scheduled job id is required")
        self._jobs[job.id] = job
        logger.info(
            "[Scheduler] registered job %s interval=%ss run_on_start=%s",
            job.id,
            self._resolve_interval_seconds(job),
            job.run_on_start,
        )
        if self._scheduler is not None:
            self._add_or_replace_job(job)

    def start(self) -> None:
        """Start APScheduler and add all registered jobs."""
        if self._scheduler is not None and getattr(self._scheduler, "running", False):
            return
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._scheduled_intervals.clear()
        for job in self._jobs.values():
            self._add_or_replace_job(job)
        self._scheduler.start()
        logger.info("[Scheduler] started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        """Stop APScheduler without waiting for long-running jobs."""
        if self._scheduler is None:
            return
        scheduler = self._scheduler
        self._scheduler = None
        self._scheduled_intervals.clear()
        shutdown_result = scheduler.shutdown(wait=False)
        if inspect.isawaitable(shutdown_result):
            await shutdown_result
        logger.info("[Scheduler] stopped")

    async def run_job_now(self, job_id: str) -> Any:
        """Run a registered job immediately; mainly useful for tests and admin hooks."""
        job = self._jobs[job_id]
        return await self._run_job(job)

    def _add_or_replace_job(self, job: ScheduledJob) -> None:
        if self._scheduler is None:
            return
        interval_seconds = self._resolve_interval_seconds(job)
        self._scheduled_intervals[job.id] = interval_seconds
        self._scheduler.add_job(
            self._make_job_runner(job.id),
            trigger=IntervalTrigger(seconds=interval_seconds),
            id=job.id,
            name=job.name or job.id,
            replace_existing=True,
            coalesce=job.coalesce,
            max_instances=job.max_instances,
            **({"next_run_time": utc_now()} if job.run_on_start else {}),
        )
        logger.info(
            "[Scheduler] scheduled job %s every %ss%s",
            job.id,
            interval_seconds,
            " starting now" if job.run_on_start else "",
        )

    def _make_job_runner(self, job_id: str) -> Callable[[], Awaitable[Any]]:
        async def _runner() -> Any:
            job = self._jobs[job_id]
            return await self._run_job(job)

        return _runner

    async def _run_job(self, job: ScheduledJob) -> Any:
        try:
            if not self._resolve_enabled(job):
                return {"skipped": True, "reason": "disabled"}
            result = await job.handler()
            return result
        except Exception as exc:
            logger.warning("[Scheduler] job %s failed: %s", job.id, exc)
            raise
        finally:
            self._refresh_interval_if_needed(job)

    def _refresh_interval_if_needed(self, job: ScheduledJob) -> None:
        if self._scheduler is None:
            return
        next_interval = self._resolve_interval_seconds(job)
        current_interval = self._scheduled_intervals.get(job.id)
        if current_interval == next_interval:
            return
        self._scheduler.reschedule_job(
            job.id,
            trigger=IntervalTrigger(seconds=next_interval),
        )
        self._scheduled_intervals[job.id] = next_interval

    @staticmethod
    def _resolve_interval_seconds(job: ScheduledJob) -> int:
        value = job.interval_seconds() if callable(job.interval_seconds) else job.interval_seconds
        return max(1, int(value))

    @staticmethod
    def _resolve_enabled(job: ScheduledJob) -> bool:
        value = job.enabled() if callable(job.enabled) else job.enabled
        return bool(value)


_runtime_scheduler: RuntimeScheduler | None = None


def get_runtime_scheduler() -> RuntimeScheduler:
    global _runtime_scheduler
    if _runtime_scheduler is None:
        _runtime_scheduler = RuntimeScheduler()
    return _runtime_scheduler
