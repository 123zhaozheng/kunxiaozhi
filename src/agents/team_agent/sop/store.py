"""SOP 计划持久化（MongoDB collection `sop_runs`）。

按 (session_id, team_id) 定位当前计划，独立于内层 message checkpointer，
支撑确认暂停、历史回放与跨会话恢复。所有写操作读回时返回完整 SOPPlan 快照。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.agents.team_agent.sop.schemas import SOPPlan, SOPPlanStatus, StepStatus
from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection

logger = get_logger(__name__)

_SOP_RUNS_COLLECTION = "sop_runs"


class SopRunStore:
    """SOP 计划快照存储：按 (session_id, team_id) 唯一定位当前计划。"""

    # 类级一次性索引初始化（进程内幂等；TEAM_SOP_MODE 可在运行时开关，故首次写入前惰性建索引）
    _indexes_initialized = False
    _indexes_lock: "asyncio.Lock | None" = None
    _run_locks: dict[tuple[str, str], asyncio.Lock] = {}
    _run_locks_guard: "asyncio.Lock | None" = None

    def __init__(self) -> None:
        self._collection: "AsyncIOMotorCollection[Any] | None" = None

    @property
    def collection(self) -> "AsyncIOMotorCollection[Any]":
        """延迟加载 MongoDB collection。"""
        if self._collection is None:
            client = get_mongo_client()
            db = client[settings.MONGODB_DB]
            self._collection = db[_SOP_RUNS_COLLECTION]
        return self._collection

    async def ensure_indexes(self) -> None:
        """创建唯一索引 (session_id, team_id)，幂等可重复调用。"""
        await self.collection.create_index(
            [("session_id", 1), ("team_id", 1)],
            unique=True,
            background=True,
        )
        logger.info("SOP runs indexes created")

    async def _ensure_indexes_if_needed(self) -> None:
        """首次写入前确保唯一索引存在；失败仅告警，下次写入重试。"""
        cls = type(self)
        if cls._indexes_initialized:
            return
        if cls._indexes_lock is None:
            cls._indexes_lock = asyncio.Lock()
        async with cls._indexes_lock:
            if cls._indexes_initialized:
                return
            try:
                await self.ensure_indexes()
            except Exception as e:
                logger.warning(f"[SopRunStore] Index initialization failed, will retry: {e}")
                return
            cls._indexes_initialized = True

    @staticmethod
    def _query(session_id: str, team_id: str) -> dict[str, str]:
        return {"session_id": session_id, "team_id": team_id}

    @classmethod
    async def _lock_for(cls, session_id: str, team_id: str) -> asyncio.Lock:
        if cls._run_locks_guard is None:
            cls._run_locks_guard = asyncio.Lock()
        async with cls._run_locks_guard:
            return cls._run_locks.setdefault((session_id, team_id), asyncio.Lock())

    async def upsert_plan(self, plan: SOPPlan) -> SOPPlan:
        """按 (session_id, team_id) 全量替换当前计划并返回完整快照；created_at 保留首次写入时间。"""
        await self._ensure_indexes_if_needed()
        lock = await self._lock_for(plan.session_id, plan.team_id)
        async with lock:
            existing = await self.get_plan(plan.session_id, plan.team_id)
            now = utc_now()
            if existing is not None:
                plan.created_at = existing.created_at
            plan.updated_at = now
            doc = plan.model_dump(mode="json")
            await self.collection.update_one(
                self._query(plan.session_id, plan.team_id),
                {"$set": doc},
                upsert=True,
            )
        return plan

    async def get_plan(self, session_id: str, team_id: str) -> SOPPlan | None:
        """读取当前计划快照；不存在返回 None。"""
        doc = await self.collection.find_one(self._query(session_id, team_id))
        if doc is None:
            return None
        doc.pop("_id", None)
        return SOPPlan(**doc)

    async def set_status(
        self, session_id: str, team_id: str, status: SOPPlanStatus
    ) -> SOPPlan | None:
        """更新计划整体状态并返回完整快照；计划不存在返回 None。"""
        lock = await self._lock_for(session_id, team_id)
        async with lock:
            result = await self.collection.update_one(
                self._query(session_id, team_id),
                {"$set": {"status": status, "updated_at": utc_now()}},
            )
            if getattr(result, "matched_count", result.modified_count) == 0:
                return None
            return await self.get_plan(session_id, team_id)

    async def set_status_for_plan(
        self,
        session_id: str,
        team_id: str,
        plan_id: str,
        status: SOPPlanStatus,
    ) -> SOPPlan | None:
        lock = await self._lock_for(session_id, team_id)
        async with lock:
            result = await self.collection.update_one(
                {**self._query(session_id, team_id), "plan_id": plan_id},
                {"$set": {"status": status, "updated_at": utc_now()}},
            )
            if getattr(result, "matched_count", result.modified_count) == 0:
                return None
            return await self.get_plan(session_id, team_id)

    async def set_step_status(
        self,
        session_id: str,
        team_id: str,
        step_id: str,
        status: StepStatus | str,
        output: str | None = None,
        error: str | None = None,
    ) -> SOPPlan | None:
        """更新指定步骤的状态/输出/错误并返回完整快照；计划或步骤不存在返回 None。"""
        lock = await self._lock_for(session_id, team_id)
        async with lock:
            next_status = status if isinstance(status, StepStatus) else StepStatus(status)
            set_fields: dict[str, Any] = {
                "steps.$.status": next_status.value,
                "updated_at": utc_now(),
            }
            if output is not None:
                set_fields["steps.$.output"] = output
            if error is not None:
                set_fields["steps.$.error"] = error
            result = await self.collection.update_one(
                {**self._query(session_id, team_id), "steps.step_id": step_id},
                {"$set": set_fields},
            )
            if getattr(result, "matched_count", result.modified_count) == 0:
                return None
            return await self.get_plan(session_id, team_id)

    async def set_feedback(
        self, session_id: str, team_id: str, feedback: str
    ) -> SOPPlan | None:
        """写入用户反馈并返回完整快照；计划不存在返回 None。"""
        lock = await self._lock_for(session_id, team_id)
        async with lock:
            result = await self.collection.update_one(
                self._query(session_id, team_id),
                {"$set": {"user_feedback": feedback, "updated_at": utc_now()}},
            )
            if getattr(result, "matched_count", result.modified_count) == 0:
                return None
            return await self.get_plan(session_id, team_id)
