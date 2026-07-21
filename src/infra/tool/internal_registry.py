"""Registry for 昆小智 internal tools exposed through the MCP UI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, get_args, get_origin

from langchain_core.tools import BaseTool

from src.infra.mcp.storage import MCPStorage
from src.infra.tool.audio_transcribe_tool import get_audio_transcribe_tool
from src.infra.tool.dify_kb_tool import get_dify_kb_retrieve_tool
from src.infra.tool.env_var_tool import get_env_var_tools
from src.infra.tool.image_generation_tool import get_image_generation_tool
from src.infra.tool.mcp_client import MCPToolWithRetry
from src.infra.tool.persona_preset_tool import get_persona_preset_tools
from src.infra.tool.read_document_tool import get_read_document_tool
from src.infra.tool.sandbox_mcp_tool import get_sandbox_mcp_tools
from src.infra.tool.team_tool import get_team_tools
from src.kernel.config import settings
from src.kernel.schemas.mcp import (
    MCPServerResponse,
    MCPToolInfo,
    MCPToolPolicy,
    MCPTransport,
)

INTERNAL_MCP_SERVER_NAME = "kunxiaozhi_internal"


def build_internal_tools() -> list[BaseTool]:
    """Build the internal tool set that 昆小智 exposes to agents."""
    tools: list[BaseTool] = []

    if settings.ENABLE_IMAGE_GENERATION:
        tools.append(get_image_generation_tool())

    if settings.ENABLE_AUDIO_TRANSCRIPTION:
        tools.append(get_audio_transcribe_tool())

    if settings.ENABLE_DOCUMENT_PARSE:
        tools.append(get_read_document_tool())

    # Dify KB retrieval requires the feature flag plus connection (base URL +
    # API key) and both model-card selections (LLM rewrite + rerank). Missing
    # any of these means the tool cannot function, so it is not exposed.
    if (
        settings.DIFY_KB_ENABLED
        and settings.DIFY_KB_BASE_URL
        and settings.DIFY_KB_API_KEY
        and settings.DIFY_KB_LLM_MODEL_ID
        and settings.DIFY_KB_RERANK_MODEL_ID
    ):
        tools.append(get_dify_kb_retrieve_tool())

    # env_var and sandbox_mcp management tools only make sense with a sandbox
    # (rebuild_sandbox_mcp / _sync_user_env_vars / EnvVarPromptMiddleware /
    # mcporter registration — all sandbox-gated). Load them only when a sandbox
    # is configured so non-sandbox deployments don't expose dead-weight tools.
    if settings.ENABLE_SANDBOX:
        tools.extend(get_env_var_tools())
        tools.extend(get_sandbox_mcp_tools())

    tools.extend(get_persona_preset_tools())
    tools.extend(get_team_tools())
    return tools


def build_internal_server_response() -> MCPServerResponse:
    """Build the virtual server row for the /mcp UI."""
    return MCPServerResponse(
        name=INTERNAL_MCP_SERVER_NAME,
        transport=MCPTransport.SANDBOX,
        enabled=True,
        is_system=True,
        is_internal=True,
        can_edit=True,
        allowed_roles=[],
        role_quotas={},
    )


def _policy_for_tool(
    policies: Mapping[str, MCPToolPolicy],
    tool_name: str,
) -> MCPToolPolicy | None:
    policy = policies.get(tool_name)
    return policy if policy is not None else None


def _is_tool_allowed(
    *,
    policy: MCPToolPolicy | None,
    user_roles: list[str] | None,
    is_admin: bool,
) -> bool:
    if is_admin:
        return True
    if policy is None:
        return True
    if policy.disabled:
        return False
    if not policy.allowed_roles:
        return True
    return bool(set(user_roles or []).intersection(policy.allowed_roles))


def _schema_type_from_annotation(annotation: Any) -> str:
    origin = get_origin(annotation)
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if origin is not None and args:
        if origin in (list, tuple, set):
            return "array"
        return _schema_type_from_annotation(args[0])
    if annotation in (list, tuple, set):
        return "array"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    if annotation is dict:
        return "object"
    return "string"


def _extract_tool_parameters(tool: BaseTool) -> list[dict[str, Any]]:
    args_schema = getattr(tool, "args_schema", None)
    if not args_schema:
        return []

    try:
        schema = args_schema if isinstance(args_schema, dict) else args_schema.schema()
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        parameters = []
        for param_name, param_info in properties.items():
            if param_name == "runtime" or not isinstance(param_info, dict):
                continue
            parameters.append(
                {
                    "name": param_name,
                    "type": param_info.get("type", "string"),
                    "description": param_info.get("description", ""),
                    "required": param_name in required,
                    "default": param_info.get("default"),
                }
            )
        return parameters
    except Exception:
        pass

    model_fields = getattr(args_schema, "model_fields", {})
    parameters = []
    for param_name, field in model_fields.items():
        if param_name == "runtime":
            continue
        default = None if field.is_required() else field.default
        parameters.append(
            {
                "name": param_name,
                "type": _schema_type_from_annotation(field.annotation),
                "description": field.description or "",
                "required": field.is_required(),
                "default": default,
            }
        )
    return parameters


async def get_internal_tool_policies() -> dict[str, MCPToolPolicy]:
    """Load explicit tool policies for the internal virtual server."""
    try:
        return await MCPStorage().list_tool_policies(INTERNAL_MCP_SERVER_NAME)
    except Exception:
        return {}


async def get_internal_tools_for_user(
    *,
    user_id: str | None,
    user_roles: list[str] | None,
    is_admin: bool,
) -> list[BaseTool]:
    """Return internal tools filtered and wrapped by per-tool policy."""
    tools = build_internal_tools()
    if not tools:
        return []

    policies = await get_internal_tool_policies()
    wrapped: list[BaseTool] = []
    for tool in tools:
        policy = _policy_for_tool(policies, tool.name)
        if not _is_tool_allowed(policy=policy, user_roles=user_roles, is_admin=is_admin):
            continue

        wrapped.append(
            MCPToolWithRetry(
                tool,
                user_id=user_id,
                server_name=INTERNAL_MCP_SERVER_NAME,
                user_roles=user_roles,
                is_admin=is_admin,
                role_quotas=(policy.role_quotas if policy else None),
                quota_tool_name=tool.name,
            )
        )
    return wrapped


async def get_internal_tool_infos(
    *,
    user_id: str | None,
    user_roles: list[str] | None,
    is_admin: bool,
) -> list[MCPToolInfo]:
    """Return tool metadata for the virtual internal server."""
    del user_id
    policies = await get_internal_tool_policies()
    infos: list[MCPToolInfo] = []
    for tool in build_internal_tools():
        policy = _policy_for_tool(policies, tool.name)
        if not _is_tool_allowed(policy=policy, user_roles=user_roles, is_admin=is_admin):
            continue

        parameters = _extract_tool_parameters(tool)

        infos.append(
            MCPToolInfo(
                name=tool.name,
                description=getattr(tool, "description", ""),
                parameters=parameters,
                system_disabled=bool(policy.disabled) if policy else False,
                user_disabled=False,
                allowed_roles=list(policy.allowed_roles) if policy else [],
                role_quotas=dict(policy.role_quotas) if policy else {},
                policy_configured=policy is not None,
            )
        )
    return infos
