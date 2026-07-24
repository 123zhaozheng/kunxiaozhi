from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, cast

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger
from src.infra.pubsub_hub import RedisPubSubHub, get_pubsub_hub
from src.infra.storage.redis import get_redis_client

from .mode import get_wecom_runtime_mode

logger = get_logger(__name__)

WECOM_CONTROL_CHANNEL = "wecom:control"
_CONTROL_RESULT_PREFIX = "wecom:control:result"
_CONTROL_RESULT_TTL_SECONDS = 30
_CONTROL_POLL_INTERVAL_SECONDS = 0.05
_DEFAULT_CONTROL_TIMEOUT_SECONDS = 5.0
_NETWORK_RESULT_PREFIX = "wecom:control:network-result"


def wecom_control_result_key(command_id: str) -> str:
    return f"{_CONTROL_RESULT_PREFIX}:{command_id}"


def wecom_network_result_key(command_id: str) -> str:
    return f"{_NETWORK_RESULT_PREFIX}:{command_id}"


async def request_wecom_reload(
    preset_id: str,
    *,
    requested_by: str | None = None,
    wait_for_result: bool = True,
    timeout_seconds: float = _DEFAULT_CONTROL_TIMEOUT_SECONDS,
) -> bool:
    """Reload a Persona's WeCom bot in the configured runtime."""
    mode = get_wecom_runtime_mode()
    if mode == "disabled":
        return False
    if mode == "embedded":
        from .manager import get_wecom_bot_manager

        manager = get_wecom_bot_manager()
        if not wait_for_result and not manager._running:
            # Startup will load the persisted config later.
            return True
        return await manager.reload_preset(preset_id)

    try:
        return await _request_external_reload(
            preset_id,
            requested_by=requested_by,
            wait_for_result=wait_for_result,
            timeout_seconds=timeout_seconds,
        )
    except Exception as e:
        logger.warning("External WeCom reload request failed for %s: %s", preset_id, e)
        return False


async def request_wecom_network_reload(
    revision: str,
    *,
    requested_by: str | None = None,
    timeout_seconds: float = 20.0,
) -> list[dict[str, Any]]:
    """Restart all bots on their owning runtime nodes and aggregate results."""
    mode = get_wecom_runtime_mode()
    if mode == "disabled":
        return []
    if mode == "embedded":
        from .manager import get_wecom_bot_manager

        manager = get_wecom_bot_manager()
        if not manager._running:
            return []
        return await manager.reload_network_config(revision)

    redis = get_redis_client()
    command_id = uuid.uuid4().hex
    payload = await run_blocking_io(
        json.dumps,
        {
            "command_id": command_id,
            "action": "reload_network",
            "revision": revision,
            "requested_by": requested_by,
        },
    )
    try:
        subscriber_count = await redis.publish(WECOM_CONTROL_CHANNEL, payload)
        if not subscriber_count:
            return []
        result_key = wecom_network_result_key(command_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout_seconds)
        while loop.time() < deadline:
            count = await cast(Awaitable[int], redis.llen(result_key))
            if count >= subscriber_count:
                break
            await asyncio.sleep(
                min(_CONTROL_POLL_INTERVAL_SECONDS, max(0.0, deadline - loop.time()))
            )
        raw_results = await cast(
            Awaitable[list[Any]],
            redis.lrange(result_key, 0, -1),
        )
        await redis.delete(result_key)
    except Exception as exc:
        logger.warning("[WeComNetwork] External network reload failed: %s", exc)
        return []

    merged: list[dict[str, Any]] = []
    for raw in raw_results:
        try:
            node_result = await run_blocking_io(json.loads, raw)
        except (json.JSONDecodeError, TypeError):
            continue
        results = node_result.get("results")
        if isinstance(results, list):
            merged.extend(item for item in results if isinstance(item, dict))
    return merged


async def _request_external_reload(
    preset_id: str,
    *,
    requested_by: str | None,
    wait_for_result: bool,
    timeout_seconds: float,
) -> bool:
    redis = get_redis_client()
    command_id = uuid.uuid4().hex
    payload = await run_blocking_io(
        json.dumps,
        {
            "command_id": command_id,
            "action": "reload_preset",
            "preset_id": preset_id,
            "requested_by": requested_by,
        },
    )
    subscriber_count = await redis.publish(WECOM_CONTROL_CHANNEL, payload)
    if not wait_for_result:
        return bool(subscriber_count)
    if not subscriber_count:
        logger.warning("No external WeCom runtime received reload command for %s", preset_id)
        return False

    result_key = wecom_control_result_key(command_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout_seconds)
    while loop.time() < deadline:
        raw_result = await redis.get(result_key)
        if raw_result:
            await redis.delete(result_key)
            try:
                result = await run_blocking_io(json.loads, raw_result)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid WeCom control result for command %s", command_id)
                return False
            return result.get("status") == "ok"
        await asyncio.sleep(min(_CONTROL_POLL_INTERVAL_SECONDS, max(0.0, deadline - loop.time())))

    logger.warning("Timed out waiting for external WeCom reload command %s", command_id)
    return False


class WeComControlListener:
    """Consume API control commands inside an external WeCom runtime."""

    def __init__(
        self,
        manager: Any,
        *,
        hub: RedisPubSubHub | None = None,
        redis: Any | None = None,
    ) -> None:
        self._manager = manager
        self._hub = hub or get_pubsub_hub()
        self._redis = redis or get_redis_client()
        self._subscription_token: str | None = None

    @property
    def is_running(self) -> bool:
        return self._subscription_token is not None

    async def start(self) -> None:
        if self._subscription_token is not None:
            return
        self._subscription_token = self._hub.subscribe(
            WECOM_CONTROL_CHANNEL,
            self._handle_message,
        )
        await self._hub.start()
        logger.info("External WeCom control listener started")

    async def stop(self) -> None:
        token = self._subscription_token
        self._subscription_token = None
        if token is None:
            return
        self._hub.unsubscribe(token)
        await self._hub.stop_if_idle()
        logger.info("External WeCom control listener stopped")

    async def _handle_message(self, message: dict[str, Any]) -> None:
        command_id = ""
        network_revision = ""
        try:
            payload = await run_blocking_io(json.loads, message["data"])
            command_id = str(payload.get("command_id") or "")
            action = payload.get("action")
            preset_id = str(payload.get("preset_id") or "")
            if not command_id:
                logger.warning("Ignoring invalid WeCom control command")
                return

            if action == "reload_network":
                network_revision = str(payload.get("revision") or "")
                if not network_revision:
                    logger.warning("Ignoring WeCom network reload without revision")
                    return
                results = await self._manager.reload_network_config(network_revision)
                await self._write_network_result(command_id, network_revision, results)
                return

            if action != "reload_preset" or not preset_id:
                logger.warning("Ignoring invalid WeCom control command")
                return

            executed = await self._manager.reload_preset(preset_id)
            if not executed:
                # Pub/sub broadcasts to every runtime. Only the preferred owner
                # acknowledges, so a non-owner cannot win the result race.
                return
            await self._write_result(
                command_id,
                status="ok",
                detail=None,
            )
        except Exception as e:
            logger.error("Failed to handle WeCom control command: %s", e)
            if command_id:
                if network_revision:
                    await self._write_network_result(command_id, network_revision, [])
                else:
                    await self._write_result(
                        command_id,
                        status="failed",
                        detail=str(e),
                    )

    async def _write_result(
        self,
        command_id: str,
        *,
        status: str,
        detail: str | None,
    ) -> None:
        payload = await run_blocking_io(
            json.dumps,
            {
                "command_id": command_id,
                "status": status,
                "node_id": getattr(self._manager, "_node_id", None),
                "detail": detail,
            },
        )
        await self._redis.set(
            wecom_control_result_key(command_id),
            payload,
            ex=_CONTROL_RESULT_TTL_SECONDS,
        )

    async def _write_network_result(
        self,
        command_id: str,
        revision: str,
        results: list[dict[str, Any]],
    ) -> None:
        payload = await run_blocking_io(
            json.dumps,
            {
                "command_id": command_id,
                "revision": revision,
                "node_id": getattr(self._manager, "_node_id", None),
                "results": results,
            },
        )
        key = wecom_network_result_key(command_id)
        await cast(Awaitable[int], self._redis.rpush(key, payload))
        await self._redis.expire(key, _CONTROL_RESULT_TTL_SECONDS)
