"""Redis coordination for scheduled tasks across replicas.

Adapted from LambChat ``src/infra/scheduler/locks.py``:

- *slot claim*: ``SET NX EX`` on ``scheduler:task_slot:{task}:{slot}``; never
  released, so a delayed peer cannot re-run the same fire slot.
- *execution lock*: random token, released with a Lua compare-and-delete so a
  replica can never release a lock it no longer owns.
"""

from __future__ import annotations

import time
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


RUN_SLOTS_KEY = "scheduler:running"
MIN_RUN_SLOT_TTL = 60

# ZSET: member = agent run id of one attempt, score = expiry timestamp (seconds).
# KEYS[1]=key; ARGV = now, limit, expire_at, member. The clock comes from the client so
# every replica uses the same time source as the score it writes.
_ACQUIRE_RUN_SLOT_LUA = """
redis.call("ZREMRANGEBYSCORE", KEYS[1], "-inf", ARGV[1])
if redis.call("ZSCORE", KEYS[1], ARGV[4]) then
    redis.call("ZADD", KEYS[1], ARGV[3], ARGV[4])
    return 1
end
if redis.call("ZCARD", KEYS[1]) < tonumber(ARGV[2]) then
    redis.call("ZADD", KEYS[1], ARGV[3], ARGV[4])
    return 1
end
return 0
"""


def _now() -> float:
    return time.time()


async def acquire_run_slot(member: str, limit: int, ttl: int) -> bool:
    """Try to take one of ``limit`` global run slots; expired members are reclaimed."""
    redis = get_redis_client()
    now = _now()
    expire_at = now + max(MIN_RUN_SLOT_TTL, int(ttl))
    result = await cast(
        Awaitable[Any],
        redis.eval(_ACQUIRE_RUN_SLOT_LUA, 1, RUN_SLOTS_KEY, now, int(limit), expire_at, member),
    )
    return bool(result)


async def release_run_slot(member: str) -> None:
    redis = get_redis_client()
    try:
        await cast(Awaitable[Any], redis.zrem(RUN_SLOTS_KEY, member))
    except Exception as exc:  # noqa: BLE001 - slot expires via score anyway
        logger.warning("[SchedulerRunSlot] release failed for member=%s: %s", member, exc)


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
