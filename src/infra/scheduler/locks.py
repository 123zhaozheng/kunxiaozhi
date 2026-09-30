"""Redis coordination for scheduled tasks across replicas.

Adapted from LambChat ``src/infra/scheduler/locks.py``:

- *slot claim*: ``SET NX EX`` on ``scheduler:task_slot:{task}:{slot}``; never
  released, so a delayed peer cannot re-run the same fire slot.
- *execution lock*: random token, released with a Lua compare-and-delete so a
  replica can never release a lock it no longer owns.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from typing import Any, Optional, cast

from src.infra.logging import get_logger
from src.infra.storage.redis import get_redis_client

logger = get_logger(__name__)

LOCK_PREFIX = "scheduler:task_lock:"
SLOT_PREFIX = "scheduler:task_slot:"
DEFAULT_LOCK_TTL = 600
DEFAULT_SLOT_TTL = 86400

_RELEASE_LOCK_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


async def claim_schedule_slot(task_id: str, slot_id: str, ttl: int = DEFAULT_SLOT_TTL) -> bool:
    redis = get_redis_client()
    acquired = await redis.set(f"{SLOT_PREFIX}{task_id}:{slot_id}", "1", nx=True, ex=ttl)
    if not acquired:
        logger.debug("[SchedulerSlot] slot %s for task %s already claimed", slot_id, task_id)
    return bool(acquired)


async def acquire_task_lock(
    task_id: str, run_id: str, ttl: int = DEFAULT_LOCK_TTL
) -> Optional[str]:
    redis = get_redis_client()
    token = f"{run_id}:{uuid.uuid4().hex[:8]}"
    acquired = await redis.set(f"{LOCK_PREFIX}{task_id}", token, nx=True, ex=max(60, int(ttl)))
    return token if acquired else None


async def release_task_lock(task_id: str, token: str) -> None:
    redis = get_redis_client()
    try:
        await cast(
            Awaitable[Any], redis.eval(_RELEASE_LOCK_LUA, 1, f"{LOCK_PREFIX}{task_id}", token)
        )
    except Exception as exc:  # noqa: BLE001 - lock expires via TTL anyway
        logger.warning("[SchedulerLock] release failed for task=%s: %s", task_id, exc)
