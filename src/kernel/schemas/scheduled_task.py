"""Scheduled task schemas (user-friendly schedule model).

Bank staff never see cron/UTC: they pick a ``ScheduleSpec`` (daily / weekdays /
weekly / monthly / once / interval) which the backend compiles into an
APScheduler trigger. See ``src/infra/scheduler/schedule_spec.py``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_TIMEZONE = "Asia/Shanghai"


class ScheduleKind(str, Enum):
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ONCE = "once"
    INTERVAL = "interval"


class ScheduledTaskStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class RunTrigger(str, Enum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class ScheduleSpec(BaseModel):
    """Friendly schedule definition.

    - ``time``: ``HH:MM`` wall-clock in the task timezone (daily/weekdays/weekly/monthly).
    - ``weekdays``: ISO weekday numbers 1(Mon)..7(Sun) for ``weekly``.
    - ``month_day``: 1..31, or -1 meaning "last day of month" for ``monthly``.
    - ``run_at``: local datetime for ``once`` (naive = task timezone).
    - ``every_hours``: 1..24 for ``interval``.
    """

    kind: ScheduleKind
    time: Optional[str] = Field(None, description="HH:MM")
    weekdays: Optional[list[int]] = None
    month_day: Optional[int] = None
    run_at: Optional[datetime] = None
    every_hours: Optional[int] = None

    @field_validator("weekdays")
    @classmethod
    def _normalize_weekdays(cls, v: Optional[list[int]]) -> Optional[list[int]]:
        if v is None:
            return v
        return sorted({int(d) for d in v})


class ScheduledTaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    prompt: str = Field(..., min_length=1, max_length=8000)
    schedule: ScheduleSpec
    timezone: str = Field(DEFAULT_TIMEZONE)
    agent_id: Literal["fast", "search"] = "fast"
    persona_preset_id: Optional[str] = None
    model_id: Optional[str] = None
    enabled: bool = True
    max_retries: int = Field(1, ge=0, le=3)
    timeout_seconds: int = Field(1800, ge=60, le=7200)
    template_id: Optional[str] = None

    @field_validator("name", "prompt")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("persona_preset_id")
    @classmethod
    def _blank_persona_to_none(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if isinstance(v, str) else v


class ScheduledTaskUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    prompt: Optional[str] = Field(None, min_length=1, max_length=8000)
    schedule: Optional[ScheduleSpec] = None
    timezone: Optional[str] = None
    agent_id: Optional[Literal["fast", "search"]] = None
    persona_preset_id: Optional[str] = None
    model_id: Optional[str] = None
    enabled: Optional[bool] = None
    max_retries: Optional[int] = Field(None, ge=0, le=3)
    timeout_seconds: Optional[int] = Field(None, ge=60, le=7200)

    @field_validator("persona_preset_id")
    @classmethod
    def _blank_persona_to_none(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if isinstance(v, str) else v


class ScheduledTask(BaseModel):
    """Persisted task document."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., alias="_id")
    owner_id: str
    name: str
    prompt: str
    schedule: ScheduleSpec
    timezone: str = DEFAULT_TIMEZONE
    agent_id: str = "fast"
    persona_preset_id: Optional[str] = None
    model_id: Optional[str] = None
    enabled: bool = True
    status: ScheduledTaskStatus = ScheduledTaskStatus.ACTIVE
    pause_reason: Optional[str] = None
    paused_by: Optional[Literal["user", "admin", "system"]] = None
    created_via: Literal["web", "chat"] = "web"
    max_retries: int = 1
    timeout_seconds: int = 1800
    template_id: Optional[str] = None
    consecutive_failures: int = 0
    total_runs: int = 0
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[RunStatus] = None
    last_run_id: Optional[str] = None
    last_session_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TaskRunRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., alias="_id")
    task_id: str
    owner_id: str
    task_name: str = ""
    trigger: RunTrigger = RunTrigger.SCHEDULE
    status: RunStatus = RunStatus.PENDING
    session_id: Optional[str] = None
    attempt: int = 0
    result_preview: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class ScheduledTaskResponse(BaseModel):
    id: str
    name: str
    prompt: str
    schedule: ScheduleSpec
    schedule_text: str
    timezone: str
    agent_id: str
    persona_preset_id: Optional[str] = None
    persona_name: Optional[str] = None
    persona_avatar: Optional[str] = None
    persona_available: Optional[bool] = None
    model_id: Optional[str] = None
    enabled: bool
    status: ScheduledTaskStatus
    pause_reason: Optional[str] = None
    paused_by: Optional[Literal["user", "admin", "system"]] = None
    created_via: Literal["web", "chat"] = "web"
    max_retries: int
    timeout_seconds: int
    template_id: Optional[str] = None
    consecutive_failures: int = 0
    total_runs: int = 0
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[RunStatus] = None
    last_run_id: Optional[str] = None
    last_session_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ScheduledTaskListResponse(BaseModel):
    items: list[ScheduledTaskResponse]
    total: int
    limit: int
    min_interval_hours: int


class TaskRunListResponse(BaseModel):
    items: list[TaskRunRecord]
    total: int


class SchedulePreviewRequest(BaseModel):
    schedule: ScheduleSpec
    timezone: str = DEFAULT_TIMEZONE
    count: int = Field(5, ge=1, le=10)


class SchedulePreviewResponse(BaseModel):
    valid: bool
    schedule_text: str = ""
    next_runs: list[datetime] = Field(default_factory=list)
    error: Optional[str] = None


class ScheduledTaskTemplate(BaseModel):
    id: str
    title: str
    description: str
    icon: str
    name: str
    prompt: str
    schedule: ScheduleSpec
    extra: dict[str, Any] = Field(default_factory=dict)
