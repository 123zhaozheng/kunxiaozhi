"""WeCom connection status API schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class WeComConnectionStatus(BaseModel):
    """Live connection status for a persona preset (from Redis or default)."""

    model_config = ConfigDict(extra="ignore")

    preset_id: str
    state: str = Field(
        ...,
        description="connected | connecting | reconnecting | disconnected | failed",
    )
    reason_code: Optional[str] = Field(
        None,
        description="replaced | reconnect_exhausted | auth_failed | lease_lost | disconnected",
    )
    reason_detail: Optional[str] = None
    updated_at: Optional[datetime] = None
    node_id: Optional[str] = None
    aibotid: Optional[str] = None


class WeComStatusBatchRequest(BaseModel):
    preset_ids: list[str] = Field(..., min_length=1, max_length=200)


class WeComStatusBatchResponse(BaseModel):
    statuses: list[WeComConnectionStatus]
