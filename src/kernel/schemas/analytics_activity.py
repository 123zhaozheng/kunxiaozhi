"""
Analytics Activity 数据模型

定义 user_daily_activity 集合的 Pydantic 模型，遵循 Base/Create/Full 约定。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from src.infra.utils.datetime import utc_now


class ActivityBase(BaseModel):
    """共享字段"""

    user_id: str = Field(..., description="用户 ID")
    date: str = Field(..., description="UTC+8 日期 (YYYY-MM-DD)")
    sources: list[str] = Field(
        default_factory=list, description="来源集合：login/message"
    )
    first_at: datetime = Field(default_factory=utc_now, description="首次活跃时间 (UTC)")
    last_at: datetime = Field(default_factory=utc_now, description="最近活跃时间 (UTC)")

    class Config:
        from_attributes = True
        populate_by_name = True


class ActivityCreate(ActivityBase):
    """创建请求 — 继承 base，不添加额外字段"""

    pass


class ActivityUpdate(BaseModel):
    """更新请求 — 所有字段可选"""

    sources: Optional[list[str]] = None
    last_at: Optional[datetime] = None


class Activity(ActivityBase):
    """完整模型 — 包含 DB 生成字段"""

    id: str = Field(..., description="_id (ObjectId)")

    class Config:
        from_attributes = True
        populate_by_name = True
