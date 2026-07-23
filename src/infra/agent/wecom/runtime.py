from __future__ import annotations

import asyncio
import signal

from src.infra.agent.wecom.control import WeComControlListener
from src.infra.agent.wecom.handler import setup_wecom_handler
from src.infra.agent.wecom.manager import get_wecom_bot_manager, stop_wecom_bots
from src.infra.agent.wecom.mode import get_wecom_runtime_mode
from src.infra.async_utils import shutdown_blocking_io_executor
from src.infra.llm.pubsub import get_model_config_pubsub
from src.infra.local_filesystem import ensure_local_filesystem_dirs
from src.infra.logging import get_logger, setup_logging
from src.infra.monitoring.event_loop import (
    start_event_loop_lag_monitor,
    stop_event_loop_lag_monitor,
)
from src.infra.settings.pubsub import get_settings_pubsub
from src.infra.storage.mongodb import close_mongo_client
from src.infra.storage.redis import close_redis_client
from src.infra.task.manager import get_task_manager
from src.infra.tool.cache_pubsub import get_tool_cache_pubsub
from src.infra.tool.mcp_global import (
    drain_background_tasks as drain_mcp_global_background_tasks,
)
from src.infra.tool.mcp_global import get_mcp_cache_pubsub
from src.infra.tool.mcp_pool import close_all_connections as close_mcp_pool_connections
from src.kernel.config import initialize_settings, settings

logger = get_logger(__name__)


async def _start_support_services() -> None:
    await start_event_loop_lag_monitor()
    await get_task_manager().start_pubsub_listener()
    await asyncio.gather(
        get_settings_pubsub().start_listener(),
        get_model_config_pubsub().start_listener(),
        get_tool_cache_pubsub().start_listener(),
        get_mcp_cache_pubsub().start_listener(),
    )


async def _stop_support_services() -> None:
    await get_task_manager().shutdown()
    await get_task_manager().stop_pubsub_listener()
    await get_mcp_cache_pubsub().stop_listener()
    await drain_mcp_global_background_tasks()
    await close_mcp_pool_connections()
    await get_tool_cache_pubsub().stop_listener()
    await get_model_config_pubsub().stop_listener()
    await get_settings_pubsub().stop_listener()
    await stop_event_loop_lag_monitor()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            # Windows' Proactor loop doesn't implement add_signal_handler.
            continue


async def run_external_wecom_runtime(
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    setup_logging()
    await initialize_settings()
    mode = get_wecom_runtime_mode()
    if mode != "external":
        raise RuntimeError(
            "External WeCom runtime requires WECOM_RUNTIME_MODE=external "
            f"(current mode: {mode})"
        )

    ensure_local_filesystem_dirs(settings)
    from src.infra.tracing import init_tracing

    init_tracing(settings)

    stop_event = stop_event or asyncio.Event()
    _install_signal_handlers(stop_event)
    control_listener: WeComControlListener | None = None

    try:
        await _start_support_services()
        await setup_wecom_handler()
        manager = get_wecom_bot_manager()
        control_listener = WeComControlListener(manager)
        await control_listener.start()
        # Reconcile once more after the control listener is live so a config
        # saved during a slow initial handshake cannot be missed.
        await manager.start()
        logger.info("External WeCom runtime started")
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.info("External WeCom runtime cancelled")
        raise
    finally:
        if control_listener is not None:
            await control_listener.stop()
        await stop_wecom_bots()
        await _stop_support_services()
        await close_mongo_client()
        await close_redis_client()
        shutdown_blocking_io_executor()
        logger.info("External WeCom runtime stopped")


def main() -> None:
    asyncio.run(run_external_wecom_runtime())


if __name__ == "__main__":
    main()
