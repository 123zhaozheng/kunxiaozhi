"""
Analytics Manager - 业务逻辑层

将前端请求参数转换为 storage 层调用，保持代码与路由清晰。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.infra.analytics.storage import AnalyticsStorage
from src.infra.analytics.usage_query import UsageFilters
from src.infra.logging import get_logger
from src.kernel.schemas.analytics import (
    ActiveUserListResponse,
    ByLabelItem,
    ByPresetFeedbackResponse,
    FeedbackListResponse,
    FeedbackSummaryResponse,
    HeatmapCell,
    OverviewResponse,
    PresetAnalyticsResponse,
    RunListResponse,
    SessionListResponse,
    SessionsTrendResponse,
    TrendDataPoint,
    UsageByPersonaItem,
    UsageByUserResponse,
    UsageInsightsResponse,
    UsageSummaryResponse,
    UsageTrendPoint,
)

logger = get_logger(__name__)


class AnalyticsManager:
    """Analytics 业务逻辑"""

    def __init__(self) -> None:
        self.storage = AnalyticsStorage()

    async def get_overview(
        self, start: datetime, end: datetime, filters: Optional[UsageFilters] = None
    ) -> OverviewResponse:
        return await self.storage.get_overview(start, end, filters)

    async def get_active_users_trend(
        self, start: datetime, end: datetime, filters: Optional[UsageFilters] = None
    ) -> list[TrendDataPoint]:
        return await self.storage.get_active_users_trend(start, end, filters)

    async def get_users_heatmap(
        self, start: datetime, end: datetime
    ) -> list[HeatmapCell]:
        return await self.storage.get_users_heatmap(start, end)

    async def get_sessions_trend(
        self, start: datetime, end: datetime, filters: Optional[UsageFilters] = None
    ) -> SessionsTrendResponse:
        return await self.storage.get_sessions_trend(start, end, filters)

    async def get_tokens_by_model(
        self, start: datetime, end: datetime
    ) -> list[ByLabelItem]:
        return await self.storage.get_tokens_by_model(start, end)

    async def get_tokens_by_preset(
        self, start: datetime, end: datetime, limit: Optional[int] = None
    ) -> list[ByLabelItem]:
        return await self.storage.get_tokens_by_preset(start, end, limit=limit or 10)

    async def get_sessions_by_agent(
        self, start: datetime, end: datetime, limit: int = 10
    ) -> list[ByLabelItem]:
        return await self.storage.get_sessions_by_agent(start, end, limit=limit)

    async def get_sessions_by_persona(
        self, start: datetime, end: datetime, limit: int = 10
    ) -> list[ByLabelItem]:
        return await self.storage.get_sessions_by_persona(start, end, limit=limit)

    async def get_tokens_trend(
        self, start: datetime, end: datetime
    ) -> list[TrendDataPoint]:
        return await self.storage.get_tokens_trend(start, end)

    async def get_preset_metrics(
        self, preset_id: str, start: datetime, end: datetime
    ) -> PresetAnalyticsResponse:
        return await self.storage.get_preset_metrics(preset_id, start, end)

    async def get_feedback_summary(
        self, start: datetime, end: datetime
    ) -> FeedbackSummaryResponse:
        return await self.storage.get_feedback_summary(start, end)

    async def get_feedback_by_preset(
        self, start: datetime, end: datetime
    ) -> ByPresetFeedbackResponse:
        return await self.storage.get_feedback_by_preset(start, end)

    async def list_sessions(
        self,
        start: datetime,
        end: datetime,
        skip: int = 0,
        limit: int = 20,
        *,
        agent_id: Optional[str] = None,
        persona_preset_id: Optional[str] = None,
        role_id: Optional[str] = None,
        sort: str = "recent",
    ) -> SessionListResponse:
        return await self.storage.list_sessions(
            start,
            end,
            skip=skip,
            limit=limit,
            agent_id=agent_id,
            persona_preset_id=persona_preset_id,
            role_id=role_id,
            sort=sort,
        )

    async def list_active_users(
        self,
        start: datetime,
        end: datetime,
        skip: int = 0,
        limit: int = 20,
        *,
        agent_id: Optional[str] = None,
        persona_preset_id: Optional[str] = None,
        role_id: Optional[str] = None,
        sort: str = "frequency",
    ) -> ActiveUserListResponse:
        return await self.storage.list_active_users(
            start,
            end,
            skip=skip,
            limit=limit,
            agent_id=agent_id,
            persona_preset_id=persona_preset_id,
            role_id=role_id,
            sort=sort,
        )

    async def list_feedback(
        self,
        start: datetime,
        end: datetime,
        preset_id: Optional[str] = None,
        rating: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> FeedbackListResponse:
        return await self.storage.list_feedback(start, end, preset_id, rating, skip, limit)

    async def list_runs(
        self,
        start: datetime,
        end: datetime,
        preset_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> RunListResponse:
        return await self.storage.list_runs(start, end, preset_id, skip, limit)

    # ── 使用情况报表（统一口径）─────────────────────────────────────

    async def build_usage_filters(
        self,
        start: datetime,
        end: datetime,
        *,
        persona_preset_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        role_id: Optional[str] = None,
    ) -> UsageFilters:
        return await self.storage.build_usage_filters(
            start,
            end,
            persona_preset_id=persona_preset_id,
            agent_id=agent_id,
            role_id=role_id,
        )

    async def get_usage_summary(self, filters: UsageFilters) -> UsageSummaryResponse:
        return await self.storage.get_usage_summary(filters)

    async def get_usage_trend(self, filters: UsageFilters) -> list[UsageTrendPoint]:
        return await self.storage.get_usage_trend(filters)

    async def get_usage_by_persona(
        self, filters: UsageFilters
    ) -> list[UsageByPersonaItem]:
        return await self.storage.get_usage_by_persona(filters)

    async def list_usage_by_user(
        self,
        filters: UsageFilters,
        skip: int = 0,
        limit: int = 20,
    ) -> UsageByUserResponse:
        return await self.storage.list_usage_by_user(filters, skip=skip, limit=limit)

    async def get_usage_insights(self, filters: UsageFilters) -> UsageInsightsResponse:
        return await self.storage.get_usage_insights(filters)
