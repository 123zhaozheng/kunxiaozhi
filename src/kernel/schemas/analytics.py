"""
Analytics 数据模型

定义 `/api/analytics` 端点所需的请求/响应结构。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OverviewResponse(BaseModel):
    """概览卡片数据"""

    active_users: int = Field(default=0, description="活跃用户数")
    total_sessions: int = Field(default=0, description="总会话数")
    total_tokens: int = Field(default=0, description="总 token 消耗")
    up_vote_rate: float = Field(default=0.0, description="点赞率 (0-100)")


class TrendDataPoint(BaseModel):
    """通用趋势数据点"""

    date: str = Field(..., description="日期 (YYYY-MM-DD)")
    value: float = Field(default=0.0, description="数值")


class TrendResponse(BaseModel):
    """趋势数据响应"""

    items: list[TrendDataPoint] = Field(default_factory=list, description="趋势数据点列表")


class HeatmapCell(BaseModel):
    """时段热力图单元格"""

    weekday: int = Field(..., description="星期 (0=周日, 6=周六)")
    hour: int = Field(..., description="小时 (0-23)")
    count: int = Field(default=0, description="请求数")


class HeatmapResponse(BaseModel):
    """时段热力图响应"""

    cells: list[HeatmapCell] = Field(default_factory=list, description="热力图单元格列表")


class ByLabelItem(BaseModel):
    """分组聚合条目"""

    label: str = Field(..., description="分组标签")
    value: float = Field(default=0.0, description="聚合数值")


class ByLabelResponse(BaseModel):
    """分组聚合响应"""

    items: list[ByLabelItem] = Field(default_factory=list, description="分组条目列表")


class SessionsTrendResponse(BaseModel):
    """会话/消息趋势响应"""

    sessions: list[TrendDataPoint] = Field(default_factory=list, description="每日会话数")
    messages: list[TrendDataPoint] = Field(default_factory=list, description="每日消息（事件）数")
    total_sessions: int = Field(default=0, description="区间总会话数")
