from __future__ import annotations

import asyncio
from typing import Any, Callable

from arq.worker import Worker

from src.infra.logging import get_logger
from src.kernel.config import settings

from .arq_payloads import TaskArqPayloadStore
from .arq_settings import build_arq_redis_settings
from .arq_worker import run_agent_task

logger = get_logger(__name__)


class EmbeddedArqRuntime:
    """Own the lifecycle of an arq worker embedded in the FastAPI process."""

    def __init__(self, worker_factory: Callable[..., Any] = Worker) -> None:
        self._worker_factory = worker_factory
        self._worker: Any | None = None
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        if getattr(settings, "TASK_BACKEND", "local") != "arq":
            return
        if not getattr(settings, "ARQ_EMBEDDED_WORKER", True):
            return

        self._worker = self._worker_factory(
            [run_agent_task],
            queue_name=settings.ARQ_QUEUE_NAME,
            redis_settings=build_arq_redis_settings(settings),
            handle_signals=False,
            max_jobs=settings.ARQ_WORKER_MAX_JOBS,
            job_timeout=settings.ARQ_JOB_TIMEOUT_SECONDS,
            ctx={"payload_store": TaskArqPayloadStore()},
            allow_abort_jobs=True,
        )
        self._task = asyncio.create_task(self._worker.async_run())
        logger.info("Embedded arq worker started")

    async def stop(self) -> None:
        if self._worker is not None:
            close = getattr(self._worker, "close", None)
            if close is not None:
                try:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
                except AttributeError as e:
                    # arq.worker.close() 无条件调用 signal.SIGUSR1，
                    # Windows 无此信号（handle_signals=False 不影响 close）。
                    if "SIGUSR1" not in str(e):
                        raise
                    logger.warning("Skip arq worker close: Windows lacks SIGUSR1")

        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._worker = None
        self._task = None


_runtime: EmbeddedArqRuntime | None = None


def get_arq_runtime() -> EmbeddedArqRuntime:
    global _runtime
    if _runtime is None:
        _runtime = EmbeddedArqRuntime()
    return _runtime


async def start_arq_runtime() -> None:
    await get_arq_runtime().start()


async def stop_arq_runtime() -> None:
    await get_arq_runtime().stop()
