"""
Analytics 数据模型

定义 `/api/analytics` 端点所需的请求/响应结构。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

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


class PresetAnalyticsResponse(BaseModel):
    """单角色智能体完整指标"""

    total_messages: int = Field(default=0, description="总消息数")
    total_sessions: int = Field(default=0, description="总会话数")
    active_users: int = Field(default=0, description="活跃用户数")
    total_tokens: int = Field(default=0, description="总 token 消耗")
    up_vote_rate: float = Field(default=0.0, description="点赞率 (0-100)")
    down_reasons: list[ByLabelItem] = Field(
        default_factory=list, description="点踩原因分布（label=原因枚举，value=数量）"
    )


class FeedbackSummaryResponse(BaseModel):
    """反馈汇总"""

    total: int = Field(default=0, description="反馈总数")
    up_count: int = Field(default=0, description="好评数")
    down_count: int = Field(default=0, description="差评数")
    up_percentage: float = Field(default=0.0, description="好评率 (0-100)")
    reason_distribution: list[ByLabelItem] = Field(
        default_factory=list, description="点踩原因分布（label=原因枚举，value=数量）"
    )


class ByPresetFeedbackItem(BaseModel):
    """按角色智能体分反馈条目"""

    preset_id: str = Field(..., description="角色智能体 ID")
    preset_name: str = Field(default="", description="角色智能体名称")
    up_count: int = Field(default=0, description="好评数")
    down_count: int = Field(default=0, description="差评数")
    total: int = Field(default=0, description="反馈总数")
    up_percentage: float = Field(default=0.0, description="好评率 (0-100)")


class ByPresetFeedbackResponse(BaseModel):
    """按角色智能体分反馈响应"""

    items: list[ByPresetFeedbackItem] = Field(default_factory=list, description="按角色分反馈条目")


class AnalyticsListMeta(BaseModel):
    """分页明细列表的公共元信息"""

    total: int = Field(default=0, description="总数")
    skip: int = Field(default=0, description="跳过条数")
    limit: int = Field(default=0, description="本次返回上限")
    has_more: bool = Field(default=False, description="是否还有更多数据")


class SessionListItem(BaseModel):
    """会话明细列表项（管理员钻取视图）"""

    id: str = Field(..., description="会话 ID")
    name: Optional[str] = Field(default=None, description="会话名称")
    user_id: Optional[str] = Field(default=None, description="用户 ID")
    agent_id: str = Field(default="default", description="Agent ID")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    is_active: bool = Field(default=True, description="是否活跃")
    task_status: Optional[str] = Field(default=None, description="任务状态")
    unread_count: int = Field(default=0, description="未读消息数")
    persona_preset_id: Optional[str] = Field(default=None, description="角色智能体 ID")
    persona_preset_name: Optional[str] = Field(default=None, description="角色智能体名称")


class SessionListResponse(AnalyticsListMeta):
    """会话明细列表响应"""

    items: list[SessionListItem] = Field(default_factory=list, description="会话条目")


class FeedbackListItem(BaseModel):
    """反馈明细列表项（管理员钻取视图）"""

    id: str = Field(..., description="反馈 ID")
    user_id: str = Field(..., description="用户 ID")
    username: str = Field(..., description="用户名")
    session_id: str = Field(..., description="会话 ID")
    run_id: str = Field(..., description="运行 ID")
    rating: str = Field(..., description="评分：up 或 down")
    comment: Optional[str] = Field(default=None, description="评论")
    reason: Optional[str] = Field(default=None, description="点踩原因")
    created_at: datetime = Field(..., description="创建时间")
    persona_preset_id: Optional[str] = Field(default=None, description="角色智能体 ID")
    persona_preset_name: Optional[str] = Field(default=None, description="角色智能体名称")


class FeedbackListResponse(AnalyticsListMeta):
    """反馈明细列表响应"""

    items: list[FeedbackListItem] = Field(default_factory=list, description="反馈条目")


class RunListItem(BaseModel):
    """运行明细列表项（含 token 用量，管理员钻取视图）"""

    run_id: str = Field(..., description="运行 ID")
    trace_id: Optional[str] = Field(default=None, description="Trace ID")
    session_id: str = Field(..., description="会话 ID")
    agent_id: str = Field(default="default", description="Agent ID")
    user_id: Optional[str] = Field(default=None, description="用户 ID")
    started_at: datetime = Field(..., description="开始时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")
    status: str = Field(default="running", description="运行状态")
    event_count: int = Field(default=0, description="事件数")
    total_tokens: int = Field(default=0, description="总 token 用量")
    persona_preset_id: Optional[str] = Field(default=None, description="角色智能体 ID")


class RunListResponse(AnalyticsListMeta):
    """运行明细列表响应"""

    items: list[RunListItem] = Field(default_factory=list, description="运行条目")
