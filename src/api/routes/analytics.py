"""
Analytics 路由 - 全局统计看板数据接口

所有端点均需 `settings:manage` 权限。
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from src.api.deps import require_permissions
from src.infra.analytics.manager import AnalyticsManager
from src.infra.logging import get_logger
from src.kernel.schemas.analytics import (
    ActiveUserListResponse,
    ByLabelResponse,
    ByPresetFeedbackResponse,
    FeedbackListResponse,
    FeedbackSummaryResponse,
    HeatmapResponse,
    OverviewResponse,
    PresetAnalyticsResponse,
    RunListResponse,
    SessionListResponse,
    SessionsTrendResponse,
    TrendResponse,
)

router = APIRouter()
logger = get_logger(__name__)

_MAX_TOP_PRESET_LIMIT = 100
_LIST_LIMIT_MAX = 100
# Export = full filtered set for list UIs; hard safety cap (surfaced as X-Export-Row-Cap).
_EXPORT_ROW_CAP = 10_000


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


@router.get("/sessions/by-agent", response_model=ByLabelResponse)
async def get_sessions_by_agent(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    limit: int = Query(10, ge=1, le=_MAX_TOP_PRESET_LIMIT, description="Top N"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> ByLabelResponse:
    """按 agent_id 聚合会话数（by-agent 维度）。"""
    s, e = _parse_range(start, end)
    items = await manager.get_sessions_by_agent(s, e, limit=limit)
    return ByLabelResponse(items=items)


@router.get("/sessions/by-persona", response_model=ByLabelResponse)
async def get_sessions_by_persona(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    limit: int = Query(10, ge=1, le=_MAX_TOP_PRESET_LIMIT, description="Top N"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> ByLabelResponse:
    """按 persona_preset_id 聚合会话数（by-persona 维度）。"""
    s, e = _parse_range(start, end)
    items = await manager.get_sessions_by_persona(s, e, limit=limit)
    return ByLabelResponse(items=items)


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


def _parse_range(start: str, end: str) -> tuple[datetime, datetime]:
    """共用：解析 start/end 并保证 e >= s。"""
    s = _parse_iso_datetime(start, name="start")
    e = _parse_iso_datetime(end, name="end")
    if e < s:
        e, s = s, e
    return s, e


def _parse_list_sort(sort: str | None, *, default: str) -> str:
    sort_mode = (sort or default).lower()
    if sort_mode not in ("recent", "frequency"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的 sort 参数，仅支持 recent 或 frequency",
        )
    return sort_mode


def _fmt_csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ";".join(str(v) for v in value if v is not None and v != "")
    return str(value)


def _csv_attachment(filename: str, headers: list[str], rows: list[list[Any]]) -> Response:
    """Build UTF-8 CSV with BOM for Excel Chinese compatibility."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_fmt_csv_cell(cell) for cell in row])
    # UTF-8 BOM so Excel opens Chinese correctly
    payload = ("﻿" + buf.getvalue()).encode("utf-8")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Row-Cap": str(_EXPORT_ROW_CAP),
        },
    )


@router.get("/presets/{preset_id}", response_model=PresetAnalyticsResponse)
async def get_preset_metrics(
    preset_id: str,
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> PresetAnalyticsResponse:
    """单角色智能体完整指标：基础 4 指标 + 点赞率 + 点踩原因分布。"""
    s, e = _parse_range(start, end)
    return await manager.get_preset_metrics(preset_id, s, e)


@router.get("/feedback/summary", response_model=FeedbackSummaryResponse)
async def get_feedback_summary(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> FeedbackSummaryResponse:
    """全局反馈汇总（含点踩原因分布）。"""
    s, e = _parse_range(start, end)
    return await manager.get_feedback_summary(s, e)


@router.get("/feedback/by-preset", response_model=ByPresetFeedbackResponse)
async def get_feedback_by_preset(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> ByPresetFeedbackResponse:
    """按角色智能体分反馈柱状图。"""
    s, e = _parse_range(start, end)
    return await manager.get_feedback_by_preset(s, e)


@router.get("/sessions/list", response_model=SessionListResponse)
async def list_sessions(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    preset_id: Optional[str] = Query(
        None, description="按角色智能体 ID 筛选（兼容旧参数，等价 persona_preset_id）"
    ),
    agent_id: Optional[str] = Query(None, description="按智能体 agent_id 筛选"),
    persona_preset_id: Optional[str] = Query(None, description="按 Persona preset ID 筛选"),
    role_id: Optional[str] = Query(None, description="按 RBAC 用户角色 ID 筛选"),
    sort: str = Query(
        "recent",
        description="排序: recent（最近，默认）| frequency（同用户会话频次）",
    ),
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(20, ge=1, le=_LIST_LIMIT_MAX, description="返回条数"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> SessionListResponse:
    """会话明细列表（时间 + agent/persona/角色筛选 + 排序 + 分页）。

    无筛选时仅按时间范围，行为与现网兼容。CSV 导出可复用同一查询参数。
    """
    s, e = _parse_range(start, end)
    sort_mode = _parse_list_sort(sort, default="recent")
    return await manager.list_sessions(
        s,
        e,
        preset_id=preset_id,
        skip=skip,
        limit=limit,
        agent_id=agent_id,
        persona_preset_id=persona_preset_id,
        role_id=role_id,
        sort=sort_mode,
    )


@router.get("/sessions/export.csv")
async def export_sessions_csv(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    preset_id: Optional[str] = Query(
        None, description="按角色智能体 ID 筛选（兼容旧参数，等价 persona_preset_id）"
    ),
    agent_id: Optional[str] = Query(None, description="按智能体 agent_id 筛选"),
    persona_preset_id: Optional[str] = Query(None, description="按 Persona preset ID 筛选"),
    role_id: Optional[str] = Query(None, description="按 RBAC 用户角色 ID 筛选"),
    sort: str = Query(
        "recent",
        description="排序: recent（最近，默认）| frequency（同用户会话频次）",
    ),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> Response:
    """导出当前筛选下的会话明细 CSV（全量，最多 _EXPORT_ROW_CAP 行）。

    查询参数与 `/sessions/list` 一致（不含 skip/limit）。UTF-8 + BOM。
    """
    s, e = _parse_range(start, end)
    sort_mode = _parse_list_sort(sort, default="recent")
    data = await manager.list_sessions(
        s,
        e,
        preset_id=preset_id,
        skip=0,
        limit=_EXPORT_ROW_CAP,
        agent_id=agent_id,
        persona_preset_id=persona_preset_id,
        role_id=role_id,
        sort=sort_mode,
    )
    headers = [
        "id",
        "name",
        "username",
        "user_id",
        "agent_id",
        "persona_preset_id",
        "persona_preset_name",
        "task_status",
        "is_active",
        "unread_count",
        "created_at",
        "updated_at",
    ]
    rows = [
        [
            item.id,
            item.name,
            item.username,
            item.user_id,
            item.agent_id,
            item.persona_preset_id,
            item.persona_preset_name,
            item.task_status,
            item.is_active,
            item.unread_count,
            item.created_at,
            item.updated_at,
        ]
        for item in data.items
    ]
    return _csv_attachment("analytics-sessions.csv", headers, rows)


@router.get("/users/list", response_model=ActiveUserListResponse)
async def list_active_users(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    agent_id: Optional[str] = Query(None, description="按智能体 agent_id 筛选"),
    persona_preset_id: Optional[str] = Query(None, description="按 Persona preset ID 筛选"),
    role_id: Optional[str] = Query(None, description="按 RBAC 用户角色 ID 筛选"),
    sort: str = Query(
        "frequency",
        description="排序: frequency（会话频次，默认）| recent（最近活跃）",
    ),
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(20, ge=1, le=_LIST_LIMIT_MAX, description="返回条数"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> ActiveUserListResponse:
    """活跃用户明细列表（时间 + agent/persona/角色筛选 + 排序 + 分页）。

    CSV 导出可复用同一查询参数。
    """
    s, e = _parse_range(start, end)
    sort_mode = _parse_list_sort(sort, default="frequency")
    return await manager.list_active_users(
        s,
        e,
        skip=skip,
        limit=limit,
        agent_id=agent_id,
        persona_preset_id=persona_preset_id,
        role_id=role_id,
        sort=sort_mode,
    )


@router.get("/users/export.csv")
async def export_users_csv(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    agent_id: Optional[str] = Query(None, description="按智能体 agent_id 筛选"),
    persona_preset_id: Optional[str] = Query(None, description="按 Persona preset ID 筛选"),
    role_id: Optional[str] = Query(None, description="按 RBAC 用户角色 ID 筛选"),
    sort: str = Query(
        "frequency",
        description="排序: frequency（会话频次，默认）| recent（最近活跃）",
    ),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> Response:
    """导出当前筛选下的活跃用户明细 CSV（全量，最多 _EXPORT_ROW_CAP 行）。

    查询参数与 `/users/list` 一致（不含 skip/limit）。UTF-8 + BOM。
    """
    s, e = _parse_range(start, end)
    sort_mode = _parse_list_sort(sort, default="frequency")
    data = await manager.list_active_users(
        s,
        e,
        skip=0,
        limit=_EXPORT_ROW_CAP,
        agent_id=agent_id,
        persona_preset_id=persona_preset_id,
        role_id=role_id,
        sort=sort_mode,
    )
    headers = [
        "username",
        "display_name",
        "user_id",
        "roles",
        "session_count",
        "last_active_at",
    ]
    rows = [
        [
            item.username,
            item.display_name,
            item.user_id,
            item.roles,
            item.session_count,
            item.last_active_at,
        ]
        for item in data.items
    ]
    return _csv_attachment("analytics-users.csv", headers, rows)


@router.get("/feedback/list", response_model=FeedbackListResponse)
async def list_feedback(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    preset_id: Optional[str] = Query(None, description="按角色智能体 ID 筛选"),
    rating: Optional[str] = Query(None, description="按评分筛选: up 或 down"),
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(20, ge=1, le=_LIST_LIMIT_MAX, description="返回条数"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> FeedbackListResponse:
    """反馈明细列表（时间 + preset + rating 筛选 + 分页）。"""
    s, e = _parse_range(start, end)
    return await manager.list_feedback(
        s, e, preset_id=preset_id, rating=rating, skip=skip, limit=limit
    )


@router.get("/runs/list", response_model=RunListResponse)
async def list_runs(
    start: str = Query(..., description="起始时间 ISO 8601 (UTC)"),
    end: str = Query(..., description="结束时间 ISO 8601 (UTC)"),
    preset_id: Optional[str] = Query(None, description="按角色智能体 ID 筛选"),
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(20, ge=1, le=_LIST_LIMIT_MAX, description="返回条数"),
    _: None = Depends(require_permissions("settings:manage")),
    manager: AnalyticsManager = Depends(get_analytics_manager),
) -> RunListResponse:
    """运行明细列表（含 token 用量，分页）。"""
    s, e = _parse_range(start, end)
    return await manager.list_runs(s, e, preset_id=preset_id, skip=skip, limit=limit)
