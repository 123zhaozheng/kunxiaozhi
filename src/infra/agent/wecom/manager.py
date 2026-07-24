"""
WeCom (企业微信) Bot manager for managing multiple bot connections.

Manages WeCom AI Bot WebSocket connections keyed by aibotid.
Each aibotid maps to one persona preset. Uses Redis lease-based distributed
coordination to ensure each aibotid is only connected from one node.
"""

import asyncio
import hashlib
import uuid
from typing import Any, Callable, Optional

from redis.asyncio import Redis

from src.infra.agent.wecom.bot import WECOM_AVAILABLE, WeComBot
from src.infra.agent.wecom.state import ConnectionState
from src.infra.agent.wecom.status import WeComStatusReasonCode, write_wecom_status
from src.infra.logging import get_logger
from src.infra.storage.redis import create_redis_client
from src.kernel.schemas.wecom import WeComGroupPolicy
from src.kernel.schemas.wecom_network import WeComNetworkBotResult, WeComNetworkConfig

logger = get_logger(__name__)
_WECOM_LEASE_PREFIX = "wecom:lease"
_WECOM_NODE_PREFIX = "wecom:nodes"
_WECOM_LEASE_TTL_SECONDS = 60
_WECOM_NODE_TTL_SECONDS = 60
_WECOM_LEASE_REFRESH_INTERVAL = 20
_WECOM_REBALANCE_INTERVAL = 20
_RELEASE_LEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""
_REFRESH_LEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("EXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
"""


class WeComBotManager:
    """
    Manager for all WeCom bot connections.

    Manages multiple WeCom AI Bot connections, one per aibotid.
    Each aibotid maps to one persona preset via the persona_wecom_config collection.
    Uses Redis lease-based distributed coordination to ensure
    each aibotid is only connected from one node.
    """

    def __init__(
        self,
        message_handler: Optional[Callable] = None,
        feedback_handler: Optional[Callable] = None,
    ):
        self.message_handler = message_handler
        self.feedback_handler = feedback_handler
        self._bots: dict[str, WeComBot] = {}  # aibotid -> WeComBot
        self._aibotid_to_preset: dict[str, str] = {}  # aibotid -> preset_id
        # Per-aibotid WeCom config (stream_reply, send_thinking_message, etc.)
        self._aibotid_configs: dict[str, dict[str, Any]] = {}
        self._running = False
        self._node_id = uuid.uuid4().hex
        self._lease_tasks: dict[str, asyncio.Task] = {}
        self._lease_redis: Redis | None = None
        self._rebalance_task: asyncio.Task | None = None
        self._network_config = WeComNetworkConfig()
        self._network_revision = "default"

    async def start(self) -> None:
        """Start all enabled WeCom bots based on persona_wecom_config."""
        if not WECOM_AVAILABLE:
            logger.warning("WeCom SDK not installed. Run: pip install wecom-aibot-sdk")
            return

        self._running = True

        from src.infra.agent.wecom.network_config import (
            get_wecom_network_config_storage,
        )

        stored_network = await get_wecom_network_config_storage().get_current()
        self._network_config = stored_network.config
        self._network_revision = stored_network.revision

        started, skipped = await self._reconcile_preset_configs()
        self._ensure_rebalance_task()
        logger.info(
            "WeCom startup processed preset configurations: started=%s skipped=%s",
            started,
            skipped,
        )

    async def stop(self) -> None:
        """Stop all WeCom bots."""
        self._running = False
        await self._cancel_rebalance_task()

        for aibotid, bot in list(self._bots.items()):
            try:
                await bot.stop()
            except Exception as e:
                logger.error("Error stopping WeCom bot aibotid=%s: %s", aibotid, e)

        await self._release_all_leases()
        await self._unregister_node()
        await self._close_lease_redis()
        self._bots.clear()
        self._aibotid_to_preset.clear()
        self._aibotid_configs.clear()

    async def reload_network_config(
        self,
        revision: str,
        *,
        authentication_timeout_seconds: float = 15.0,
    ) -> list[dict[str, Any]]:
        """Restart locally owned bots using one persisted network revision."""
        from src.infra.agent.wecom.network_config import (
            get_wecom_network_config_storage,
        )

        stored = await get_wecom_network_config_storage().get_current()
        if stored.revision != revision:
            logger.warning(
                "[WeComNetwork] Ignoring stale reload revision=%s current=%s",
                revision,
                stored.revision,
            )
            return []

        self._network_config = stored.config
        self._network_revision = stored.revision

        for aibotid in list(self._bots):
            await self._stop_bot(aibotid)
        self._aibotid_to_preset.clear()
        self._aibotid_configs.clear()
        await self._reconcile_preset_configs()

        from src.infra.agent.config_storage import get_agent_config_storage

        node_ids = await self._list_active_node_ids()
        if self._node_id not in node_ids:
            node_ids.append(self._node_id)
            node_ids.sort()
        raw_configs = await get_agent_config_storage().get_all_persona_wecom_configs_raw()

        async def _result_for_config(
            config: dict[str, Any],
        ) -> dict[str, Any] | None:
            aibotid = str(config.get("aibotid") or "")
            preset_id = str(config.get("preset_id") or "")
            if aibotid:
                if self._preferred_owner(aibotid, node_ids) != self._node_id:
                    return None
            elif node_ids and node_ids[0] != self._node_id:
                return None

            bot = self._bots.get(aibotid)
            if bot is None:
                return WeComNetworkBotResult(
                    preset_id=preset_id,
                    aibotid=aibotid,
                    state=ConnectionState.FAILED.value,
                    reason_code=WeComStatusReasonCode.DISCONNECTED.value,
                    reason_detail=(
                        "invalid_bot_configuration"
                        if not aibotid or not config.get("secret")
                        else "bot_start_failed"
                    ),
                    node_id=self._node_id,
                ).model_dump()

            authenticated = await bot.wait_until_authenticated(authentication_timeout_seconds)
            state = bot._get_connection_state()
            result = WeComNetworkBotResult(
                preset_id=preset_id,
                aibotid=aibotid,
                state=ConnectionState.CONNECTED.value if authenticated else state.value,
                reason_code=(
                    None
                    if authenticated
                    else WeComStatusReasonCode.AUTH_FAILED.value
                    if state == ConnectionState.FAILED
                    else WeComStatusReasonCode.DISCONNECTED.value
                ),
                reason_detail=None if authenticated else "authentication_timeout_or_failure",
                node_id=self._node_id,
            )
            return result.model_dump()

        local_results = await asyncio.gather(
            *(_result_for_config(config) for config in raw_configs)
        )
        return [result for result in local_results if result is not None]

    async def _reconcile_preset_configs(self) -> tuple[int, int]:
        """Load persona_wecom_config entries and start bots for entries assigned to this node."""
        if not await self._refresh_node_membership():
            return 0, 0

        node_ids = await self._list_active_node_ids()
        if self._node_id not in node_ids:
            node_ids.append(self._node_id)
            node_ids.sort()

        started = 0
        skipped = 0
        desired_aibotids: set[str] = set()

        # Load all persona WeCom configs from AgentConfigStorage
        from src.infra.agent.config_storage import get_agent_config_storage

        storage = get_agent_config_storage()
        all_configs = await storage.get_all_persona_wecom_configs_raw()

        for config in all_configs:
            try:
                aibotid = config.get("aibotid", "")
                secret = config.get("secret", "")
                preset_id = config.get("preset_id", "")

                if not aibotid or not secret:
                    logger.warning(
                        "Skipping preset WeCom config for preset_id=%s: missing aibotid or secret",
                        preset_id,
                    )
                    skipped += 1
                    continue

                if self._preferred_owner(aibotid, node_ids) != self._node_id:
                    # This bot should run on another node
                    if aibotid in self._bots:
                        await self._stop_bot(aibotid)
                    skipped += 1
                    continue

                desired_aibotids.add(aibotid)

                # Store config metadata for handler access
                self._aibotid_to_preset[aibotid] = preset_id
                self._aibotid_configs[aibotid] = config

                if await self._start_bot(aibotid, secret, replace_existing=False):
                    started += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(
                    "Failed to reconcile WeCom config for preset_id=%s: %s",
                    config.get("preset_id"),
                    e,
                )
                skipped += 1

        # Stop bots that are no longer desired
        for aibotid in list(self._bots.keys()):
            if aibotid not in desired_aibotids:
                await self._stop_bot(aibotid)

        return started, skipped

    async def reload_preset(self, preset_id: str) -> bool:
        """Reload a preset's WeCom configuration and restart the bot if needed.

        Called when a preset's WeCom config is updated or deleted via API.
        """
        from src.infra.agent.config_storage import get_agent_config_storage

        storage = get_agent_config_storage()

        # Find the old aibotid for this preset (if any)
        old_aibotid = None
        for aid, pid in self._aibotid_to_preset.items():
            if pid == preset_id:
                old_aibotid = aid
                break

        # Stop the old bot if it exists
        if old_aibotid:
            await self._stop_bot(old_aibotid)
            del self._aibotid_to_preset[old_aibotid]
            self._aibotid_configs.pop(old_aibotid, None)

        # Load the new config
        config = await storage.get_persona_wecom_config(preset_id)
        if not config or not config.aibotid:
            # Config was deleted or has no aibotid
            logger.info("[WeCom] Preset %s WeCom config removed", preset_id)
            return True

        # Need the raw config (with secret) to start the bot
        all_raw = await storage.get_all_persona_wecom_configs_raw()
        raw_config = None
        for rc in all_raw:
            if rc.get("preset_id") == preset_id:
                raw_config = rc
                break

        if not raw_config or not raw_config.get("secret"):
            logger.warning("[WeCom] Preset %s has no secret, cannot start bot", preset_id)
            return True

        aibotid = raw_config["aibotid"]
        secret = raw_config["secret"]

        # Check if this node should own this bot
        if await self._refresh_node_membership():
            nodes = await self._list_active_node_ids()
            if self._node_id not in nodes:
                nodes.append(self._node_id)
            if self._preferred_owner(aibotid, sorted(nodes)) != self._node_id:
                logger.info("[WeCom] Preset %s bot should run on another node", preset_id)
                return False

        # Store config and start
        self._aibotid_to_preset[aibotid] = preset_id
        self._aibotid_configs[aibotid] = raw_config
        return await self._start_bot(aibotid, secret)

    def get_preset_id_for_aibotid(self, aibotid: str) -> str | None:
        """Look up preset_id for a given aibotid."""
        return self._aibotid_to_preset.get(aibotid)

    async def _publish_bot_status(
        self,
        aibotid: str,
        *,
        state: ConnectionState,
        reason_code: WeComStatusReasonCode | str | None = None,
        reason_detail: str | None = None,
        expected_revision: str | None = None,
    ) -> None:
        if expected_revision is not None and expected_revision != self._network_revision:
            return
        preset_id = self._aibotid_to_preset.get(aibotid)
        if not preset_id:
            return
        await write_wecom_status(
            preset_id,
            state=state,
            reason_code=reason_code,
            reason_detail=reason_detail,
            node_id=self._node_id,
            aibotid=aibotid,
            network_revision=self._network_revision,
        )

    async def _write_lease_lost_status(self, aibotid: str) -> None:
        preset_id = self._aibotid_to_preset.get(aibotid)
        if not preset_id:
            return
        await write_wecom_status(
            preset_id,
            state=ConnectionState.DISCONNECTED,
            reason_code=WeComStatusReasonCode.LEASE_LOST,
            reason_detail="lease_refresh_lost",
            node_id=self._node_id,
            aibotid=aibotid,
            network_revision=self._network_revision,
        )

    def get_config_for_aibotid(self, aibotid: str) -> dict[str, Any] | None:
        """Look up WeCom config for a given aibotid."""
        return self._aibotid_configs.get(aibotid)

    def find_bot(self, aibotid: str) -> WeComBot | None:
        """Find a WeComBot by its aibotid."""
        return self._bots.get(aibotid)

    async def send_message(self, aibotid: str, chat_id: str, content: str) -> bool:
        """Send a message through a WeCom bot."""
        bot = self._bots.get(aibotid)
        if not bot:
            logger.warning("No WeCom bot for aibotid=%s", aibotid)
            return False
        return await bot.send_message(chat_id, content)

    # -- Node membership & rebalance --

    async def _refresh_node_membership(self) -> bool:
        try:
            redis = self._get_lease_redis()
            await redis.set(
                self._node_key(self._node_id),
                self._node_id,
                ex=_WECOM_NODE_TTL_SECONDS,
            )
            return True
        except Exception as e:
            logger.warning("[WeCom] Failed to refresh node membership: %s", e)
            return False

    async def _list_active_node_ids(self) -> list[str]:
        redis = self._get_lease_redis()
        pattern = self._node_key("*")
        cursor: int | str = 0
        node_ids: set[str] = set()
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=100)
            for key in keys:
                key_text = key.decode() if isinstance(key, bytes) else str(key)
                node_id = key_text.rsplit(":", 1)[-1]
                if node_id:
                    node_ids.add(node_id)
            if int(cursor) == 0:
                return sorted(node_ids)

    async def _unregister_node(self) -> None:
        try:
            redis = self._get_lease_redis()
            await redis.delete(self._node_key(self._node_id))
        except Exception as e:
            logger.warning("[WeCom] Failed to unregister node membership: %s", e)

    def _ensure_rebalance_task(self) -> None:
        if self._rebalance_task and not self._rebalance_task.done():
            return
        self._rebalance_task = asyncio.create_task(self._rebalance_loop())

    async def _rebalance_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(_WECOM_REBALANCE_INTERVAL)
                await self._reconcile_preset_configs()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("[WeCom] Rebalance loop stopped unexpectedly: %s", e)
        finally:
            self._rebalance_task = None

    async def _cancel_rebalance_task(self) -> None:
        task = self._rebalance_task
        self._rebalance_task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _node_key(node_id: str) -> str:
        return f"{_WECOM_NODE_PREFIX}:{node_id}"

    @staticmethod
    def _preferred_owner(aibotid: str, node_ids: list[str]) -> str | None:
        if not node_ids:
            return None
        return max(
            node_ids,
            key=lambda node_id: hashlib.sha256(f"{aibotid}:{node_id}".encode()).hexdigest(),
        )

    # -- Bot lifecycle --

    async def _start_bot(
        self,
        aibotid: str,
        secret: str,
        *,
        replace_existing: bool = True,
    ) -> bool:
        """Start a WeComBot for the given aibotid."""
        existing_bot = self._bots.get(aibotid)
        existing_running = existing_bot is not None and existing_bot.is_running

        if existing_bot and not replace_existing and existing_running:
            existing_bot.message_handler = self.message_handler
            existing_bot.feedback_handler = self.feedback_handler
            self._ensure_lease_refresh_task(aibotid)
            await self._publish_bot_status(
                aibotid,
                state=existing_bot._get_connection_state(),
            )
            return True

        if not await self._acquire_lease(aibotid):
            logger.info(
                "[WeCom] Lease for aibotid=%s is held by another instance, skipping",
                aibotid,
            )
            return False

        try:
            if aibotid in self._bots:
                await self._bots[aibotid].stop()

            config = self._aibotid_configs.get(aibotid, {})
            bot_revision = self._network_revision
            bot = WeComBot(
                aibotid=aibotid,
                secret=secret,
                network_config=self._network_config,
                group_policy=WeComGroupPolicy(config.get("group_policy", "mention")),
                message_handler=self.message_handler,
                feedback_handler=self.feedback_handler,
                status_callback=lambda aid, **kwargs: self._publish_bot_status(
                    aid,
                    expected_revision=bot_revision,
                    **kwargs,
                ),
            )
            success = await bot.start()

            if success:
                self._bots[aibotid] = bot
                self._ensure_lease_refresh_task(aibotid)
                return True
            await self._release_lease(aibotid)
            return False
        except BaseException:
            await self._release_lease(aibotid)
            raise

    async def _stop_bot(self, aibotid: str) -> None:
        """Stop a WeComBot and release its lease."""
        bot = self._bots.pop(aibotid, None)
        if not bot:
            return

        try:
            await bot.stop()
        except Exception as e:
            logger.error("Error stopping WeCom bot aibotid=%s: %s", aibotid, e)

        await self._release_lease(aibotid)

    # -- Redis lease management --

    @staticmethod
    def _lease_key(aibotid: str) -> str:
        return f"{_WECOM_LEASE_PREFIX}:{aibotid}"

    async def _acquire_lease(self, aibotid: str) -> bool:
        try:
            redis = self._get_lease_redis()
            claimed = await redis.set(
                self._lease_key(aibotid),
                self._node_id,
                nx=True,
                ex=_WECOM_LEASE_TTL_SECONDS,
            )
            return bool(claimed)
        except Exception as e:
            logger.warning("[WeCom] Failed to acquire lease for aibotid=%s: %s", aibotid, e)
            return False

    def _ensure_lease_refresh_task(self, aibotid: str) -> None:
        if aibotid in self._lease_tasks:
            return

        async def _refresh() -> None:
            try:
                redis = self._get_lease_redis()
                while True:
                    await asyncio.sleep(_WECOM_LEASE_REFRESH_INTERVAL)
                    if aibotid not in self._bots:
                        return
                    refreshed = await redis.eval(
                        _REFRESH_LEASE_LUA,
                        1,
                        self._lease_key(aibotid),
                        self._node_id,
                        _WECOM_LEASE_TTL_SECONDS,
                    )
                    if not refreshed:
                        logger.warning(
                            "[WeCom] Lost lease refresh for aibotid=%s on instance=%s",
                            aibotid,
                            self._node_id,
                        )
                        await self._stop_bot_after_lost_lease(aibotid)
                        return
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("[WeCom] Lease refresh failed for aibotid=%s: %s", aibotid, e)
                await self._stop_bot_after_lost_lease(aibotid)
            finally:
                self._lease_tasks.pop(aibotid, None)

        self._lease_tasks[aibotid] = asyncio.create_task(_refresh())

    async def _stop_bot_after_lost_lease(self, aibotid: str) -> None:
        bot = self._bots.pop(aibotid, None)
        if not bot:
            return

        try:
            await bot.stop()
            logger.warning(
                "[WeCom] Stopped bot for aibotid=%s after losing lease",
                aibotid,
            )
        except Exception as e:
            logger.error(
                "[WeCom] Failed to stop bot for aibotid=%s after losing lease: %s",
                aibotid,
                e,
            )
        await self._write_lease_lost_status(aibotid)

    async def _release_lease(self, aibotid: str) -> None:
        task = self._lease_tasks.pop(aibotid, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            redis = self._get_lease_redis()
            await redis.eval(_RELEASE_LEASE_LUA, 1, self._lease_key(aibotid), self._node_id)
        except Exception as e:
            logger.warning("[WeCom] Failed to release lease for aibotid=%s: %s", aibotid, e)

    def _cancel_all_lease_tasks(self) -> None:
        for aibotid in list(self._lease_tasks.keys()):
            task = self._lease_tasks.pop(aibotid, None)
            if task and not task.done():
                task.cancel()

    async def _release_all_leases(self) -> None:
        for aibotid in list(self._bots.keys()):
            await self._release_lease(aibotid)

    def _get_lease_redis(self):
        if self._lease_redis is None:
            self._lease_redis = create_redis_client(isolated_pool=True)
        return self._lease_redis

    async def _close_lease_redis(self) -> None:
        if self._lease_redis is None:
            return
        try:
            await self._lease_redis.aclose()
        except Exception as e:
            logger.warning("[WeCom] Failed to close lease redis client: %s", e)
        finally:
            self._lease_redis = None


# Global instance
_wecom_bot_manager: Optional[WeComBotManager] = None


def get_wecom_bot_manager() -> WeComBotManager:
    """Get the global WeCom bot manager instance."""
    global _wecom_bot_manager
    if _wecom_bot_manager is None:
        _wecom_bot_manager = WeComBotManager()
    return _wecom_bot_manager


async def start_wecom_bots(message_handler=None, feedback_handler=None) -> None:
    """Start the WeCom bot manager with all preset-configured bots."""
    manager = get_wecom_bot_manager()
    manager.message_handler = message_handler
    manager.feedback_handler = feedback_handler
    await manager.start()


async def stop_wecom_bots() -> None:
    """Stop the WeCom bot manager."""
    global _wecom_bot_manager
    if _wecom_bot_manager:
        await _wecom_bot_manager.stop()
        _wecom_bot_manager = None
