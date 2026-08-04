"""WeCom 点赞/点踩通知目标 API schemas."""

from pydantic import BaseModel


class NotifyTargetItem(BaseModel):
    """通知目标及其绑定状态"""

    username: str
    bound: bool


class NotifyTargetsResponse(BaseModel):
    """通知目标列表（含绑定状态）"""

    targets: list[NotifyTargetItem]


class NotifyTargetsUpdate(BaseModel):
    """全量更新通知目标列表"""

    targets: list[str]
