"""WeCom (企业微信) AI Bot persona-preset configuration schemas.

The old instance-model schemas (WeComConfigBase, WeComConfig, etc.) have been
removed as part of the WeCom instance-to-role refactoring.
The role-level schemas were later migrated to persona-preset level.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

WECOM_SEGMENT_TARGET_CHARS_MIN = 100
WECOM_SEGMENT_TARGET_CHARS_MAX = 600
WECOM_DEFAULT_SEGMENT_TARGET_CHARS = 600


class WeComGroupPolicy(str, Enum):
    """Group message handling policy."""

    OPEN = "open"  # Respond to all group messages
    MENTION = "mention"  # Respond only when @mentioned


# ============================================
# Persona WeCom Config Schemas
# ============================================


class PersonaWeComConfigBase(BaseModel):
    """Base schema for persona-preset-level WeCom configuration."""

    aibotid: str = Field(..., description="企业微信机器人 bot_id")
    secret: str = Field(..., description="企业微信机器人密钥")
    stream_reply: bool = Field(True, description="通过 WebSocket 流式回复")
    send_thinking_message: bool = Field(
        True, description="在 5 秒回调期限内发送思考占位消息"
    )
    segmented_reply: bool = Field(True, description="超长回复自动分段发送")
    segment_target_chars: int = Field(
        WECOM_DEFAULT_SEGMENT_TARGET_CHARS,
        ge=WECOM_SEGMENT_TARGET_CHARS_MIN,
        le=WECOM_SEGMENT_TARGET_CHARS_MAX,
        description="分段回复每段目标字符数；实际仍受企业微信 UTF-8 字节上限约束",
    )
    session_ttl_hours: int = Field(24, description="会话 TTL 小时数，0 表示永不过期")
    feedback_notify_targets: list[str] = Field(
        default_factory=list,
        description="点赞/点踩通知对象（企业微信 userid / 昆小智 username 列表，空则不通知）",
    )


class PersonaWeComConfigCreate(PersonaWeComConfigBase):
    """Schema for creating persona-preset-level WeCom configuration."""

    pass


class PersonaWeComConfigUpdate(BaseModel):
    """Schema for updating persona-preset-level WeCom configuration."""

    model_config = ConfigDict(extra="forbid")

    aibotid: Optional[str] = None
    secret: Optional[str] = None
    stream_reply: Optional[bool] = None
    send_thinking_message: Optional[bool] = None
    segmented_reply: Optional[bool] = None
    segment_target_chars: Optional[int] = Field(
        None,
        ge=WECOM_SEGMENT_TARGET_CHARS_MIN,
        le=WECOM_SEGMENT_TARGET_CHARS_MAX,
    )
    session_ttl_hours: Optional[int] = None
    feedback_notify_targets: Optional[list[str]] = None


class PersonaWeComConfig(BaseModel):
    """Persona-preset-level WeCom configuration (database view)."""

    preset_id: str
    aibotid: str
    has_secret: bool = True
    stream_reply: bool = True
    send_thinking_message: bool = True
    segmented_reply: bool = True
    segment_target_chars: int = WECOM_DEFAULT_SEGMENT_TARGET_CHARS
    session_ttl_hours: int = 24
    feedback_notify_targets: list[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
