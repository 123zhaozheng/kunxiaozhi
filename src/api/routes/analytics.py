"""
Analytics 路由 - 全局统计看板数据接口

所有端点均需 `settings:manage` 权限。
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import require_permissions
from src.infra.analytics.manager import AnalyticsManager
from src.infra.logging import get_logger
from src.kernel.schemas.analytics import (
    ByLabelResponse,
    HeatmapResponse,
    OverviewResponse,
    SessionsTrendResponse,
    TrendResponse,
)

router = APIRouter()
logger = get_logger(__name__)

_MAX_TOP_PRESET_LIMIT = 100


@lru_cache
def get_analytics_manager() -> AnalyticsManager:
    """获取 analytics 管理器依赖（单例）"""
    return AnalyticsManager()


def _parse_iso_datetime(value: str, *, name: str) -> datetime:
    """解析 ISO 8601 字符串为带 UTC 时区的 datetime。"""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        name_label = {"start": "开始", "end": "结束"}.get(name, name)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 {name_label} 时间: {value}",
        ) from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> OverviewResponse:
    """概览卡片：活跃用户 / 总会话 / 总 token / 点赞率。"""
    s = _parse_iso_datetime(start, name="start")
    e = _parse_iso_datetime(end, name="end")
    if e < s:
        e, s = s, e
    return await manager.get_overview(s, e)


@router.get("/users/active", response_model=TrendResponse)
async def get_users_active_trend(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> TrendResponse:
    """按天统计的活跃用户折线图。"""
    s = _parse_iso_datetime(start, name="start")
    e = _parse_iso_datetime(end, name="end")
    if e < s:
        e, s = s, e
    items = await manager.get_active_users_trend(s, e)
    return TrendResponse(items=items)


@router.get("/users/heatmap", response_model=HeatmapResponse)
async def get_users_heatmap(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> HeatmapResponse:
    """按星期×小时绘制的请求热力图。"""
    s = _parse_iso_datetime(start, name="start")
    e = _parse_iso_datetime(end, name="end")
    if e < s:
        e, s = s, e
    cells = await manager.get_users_heatmap(s, e)
    return HeatmapResponse(cells=cells)


@router.get("/sessions/trend", response_model=SessionsTrendResponse)
async def get_sessions_trend(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> SessionsTrendResponse:
    """会话 + 消息趋势。"""
    s = _parse_iso_datetime(start, name="start")
    e = _parse_iso_datetime(end, name="end")
    if e < s:
        e, s = s, e
    return await manager.get_sessions_trend(s, e)


@router.get("/tokens/by-model", response_model=ByLabelResponse)
async def get_tokens_by_model(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> ByLabelResponse:
    """按模型统计 token 消耗。"""
    s = _parse_iso_datetime(start, name="start")
    e = _parse_iso_datetime(end, name="end")
    if e < s:
        e, s = s, e
    items = await manager.get_tokens_by_model(s, e)
    return ByLabelResponse(items=items)


@router.get("/tokens/by-preset", response_model=ByLabelResponse)
async def get_tokens_by_preset(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    limit: int = Query(10, ge=1, le=_MAX_TOP_PRESET_LIMIT, description="Top N"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> ByLabelResponse:
    """按 Agent 类型统计 Top N token 消耗。

    traces.agent_id 无法关联到 persona_presets，故按 Agent 类型聚合（路由路径保留不变）。
    """
    s = _parse_iso_datetime(start, name="start")
    e = _parse_iso_datetime(end, name="end")
    if e < s:
        e, s = s, e
    items = await manager.get_tokens_by_preset(s, e, limit=limit)
    return ByLabelResponse(items=items)


@router.get("/tokens/trend", response_model=TrendResponse)
async def get_tokens_trend(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> TrendResponse:
    """按天统计的 token 消耗折线图。"""
    s = _parse_iso_datetime(start, name="start")
    e = _parse_iso_datetime(end, name="end")
    if e < s:
        e, s = s, e
    items = await manager.get_tokens_trend(s, e)
    return TrendResponse(items=items)
