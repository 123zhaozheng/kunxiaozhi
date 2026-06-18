"""
Analytics Manager - 业务逻辑层

将前端请求参数转换为 storage 层调用，保持代码与路由清晰。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.infra.analytics.storage import AnalyticsStorage
from src.infra.logging import get_logger
from src.kernel.schemas.analytics import (
    ByLabelItem,
    HeatmapCell,
    OverviewResponse,
    SessionsTrendResponse,
    TrendDataPoint,
)

logger = get_logger(__name__)


class AnalyticsManager:
    """Analytics 业务逻辑"""

    def __init__(self) -> None:
        self.storage = AnalyticsStorage()

    async def get_overview(
        self, start: datetime, end: datetime
    ) -> OverviewResponse:
        return await self.storage.get_overview(start, end)

    async def get_active_users_trend(
        self, start: datetime, end: datetime
    ) -> list[TrendDataPoint]:
        return await self.storage.get_active_users_trend(start, end)

    async def get_users_heatmap(
        self, start: datetime, end: datetime
    ) -> list[HeatmapCell]:
        return await self.storage.get_users_heatmap(start, end)

    async def get_sessions_trend(
        self, start: datetime, end: datetime
    ) -> SessionsTrendResponse:
        return await self.storage.get_sessions_trend(start, end)

    async def get_tokens_by_model(
        self, start: datetime, end: datetime
    ) -> list[ByLabelItem]:
        return await self.storage.get_tokens_by_model(start, end)

    async def get_tokens_by_preset(
        self, start: datetime, end: datetime, limit: Optional[int] = None
    ) -> list[ByLabelItem]:
        return await self.storage.get_tokens_by_preset(start, end, limit=limit or 10)

    async def get_tokens_trend(
        self, start: datetime, end: datetime
    ) -> list[TrendDataPoint]:
        return await self.storage.get_tokens_trend(start, end)
