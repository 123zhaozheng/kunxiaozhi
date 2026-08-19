"""Strict API contracts for managed OpenSandbox nodes and inventory."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.kernel.exceptions import SandboxCapacityUnavailable as OpenSandboxCapacityUnavailable

__all__ = ["OpenSandboxCapacityUnavailable"]


class OpenSandboxMode(str, Enum):
    LEGACY = "legacy"
    MULTI_NODE = "multi_node"


class OpenSandboxNodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    domain: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, repr=False)
    clear_api_key: bool = False
    image: str = Field(default="ubuntu", min_length=1, max_length=256)
    timeout: int = Field(default=3600, ge=1, le=7 * 24 * 3600)
    work_dir: str = Field(default="/root", min_length=1, max_length=512)
    use_server_proxy: bool = True
    max_sandboxes: int = Field(default=1, ge=1, le=100000)
    enabled: bool = True
    priority: int = Field(default=100, ge=-100000, le=100000)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("domain must use http or https and include a host")
        if parsed.username or parsed.password:
            raise ValueError("domain must not contain credentials")
        return value.rstrip("/")


class OpenSandboxNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    domain: str
    has_api_key: bool = False
    image: str
    timeout: int
    work_dir: str
    use_server_proxy: bool
    max_sandboxes: int
    enabled: bool
    priority: int
    draining: bool = False
    health_state: str = "unknown"
    last_health_at: datetime | None = None
    last_error: str | None = None
    used_sandboxes: int = 0
    over_capacity: bool = False


class OpenSandboxNodesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: OpenSandboxMode = OpenSandboxMode.MULTI_NODE
    nodes: list[OpenSandboxNodeInput] = Field(default_factory=list)
    expected_revision: str | None = None

    @model_validator(mode="after")
    def unique_nodes(self) -> "OpenSandboxNodesUpdate":
        ids = [node.id for node in self.nodes]
        domains = [node.domain.lower() for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        if len(domains) != len(set(domains)):
            raise ValueError("node domains must be unique")
        if self.mode == OpenSandboxMode.MULTI_NODE and not self.nodes:
            raise ValueError("multi_node mode requires at least one node")
        return self


class OpenSandboxNodesResponse(BaseModel):
    revision: str
    mode: OpenSandboxMode
    nodes: list[OpenSandboxNodeResponse] = Field(default_factory=list)
    updated_at: datetime | None = None
    updated_by: str | None = None


class OpenSandboxProbeResponse(BaseModel):
    node_id: str
    health_state: str
    latency_ms: float | None = None
    detail: str | None = None


class OpenSandboxSandboxState(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    CREATING = "creating"
    TERMINATED = "terminated"
    UNKNOWN = "unknown"


class OpenSandboxInventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    sandbox_id: str
    state: str
    managed: bool
    user_id: str | None = None
    username: str | None = None
    binding_state: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    actions: dict[str, bool] = Field(default_factory=dict)


class OpenSandboxInventoryResponse(BaseModel):
    items: list[OpenSandboxInventoryItem] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 50
