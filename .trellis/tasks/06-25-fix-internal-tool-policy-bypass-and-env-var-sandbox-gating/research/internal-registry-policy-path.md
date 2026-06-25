# Research: The internal_registry Policy Path

- **Query**: Trace `get_internal_tools_for_user` → `build_internal_tools` → `_is_tool_allowed` → `MCPToolPolicy` → `MCPStorage.set_tool_policy` / `list_tool_policies`. What does toggling a tool in the UI write to DB? What does `_is_tool_allowed` check? How does `MCPToolWithRetry` wrapping differ from a raw `@tool`?
- **Scope**: internal
- **Date**: 2026-06-25

## Call chain overview

```
context.setup()
  └─ resolve_user_mcp_access(user_id)            # src/infra/mcp/quota.py:214
  └─ get_internal_tools_for_user(...)            # src/infra/tool/internal_registry.py:155
       ├─ build_internal_tools()                 # internal_registry.py:28
       ├─ get_internal_tool_policies()           # internal_registry.py:147
       │    └─ MCPStorage().list_tool_policies(INTERNAL_MCP_SERVER_NAME)  # storage.py:603
       ├─ _policy_for_tool(policies, tool.name)  # internal_registry.py:58
       ├─ _is_tool_allowed(policy, user_roles, is_admin)  # internal_registry.py:66
       └─ MCPToolWithRetry(tool, ...)            # internal_registry.py:173
```

## 1. `build_internal_tools()` — `src/infra/tool/internal_registry.py:28-41`

Builds the raw `@tool` objects (NO policy, NO wrapping):

```python
def build_internal_tools() -> list[BaseTool]:
    tools: list[BaseTool] = []
    if settings.ENABLE_IMAGE_GENERATION:
        tools.append(get_image_generation_tool())
    if settings.ENABLE_AUDIO_TRANSCRIPTION:
        tools.append(get_audio_transcribe_tool())
    tools.extend(get_env_var_tools())
    tools.extend(get_persona_preset_tools())
    tools.extend(get_team_tools())
    return tools
```

Tool names produced:
- `image_generate` (only if `ENABLE_IMAGE_GENERATION`)
- `audio_transcribe` (only if `ENABLE_AUDIO_TRANSCRIPTION`)
- `env_var_list`, `env_var_set`, `env_var_delete`, `env_var_delete_all` (always, no guard)
- `create_persona_preset`, `update_persona_preset` (always)
- `search_persona_presets`, `create_agent_team` (always)

## 2. `get_internal_tool_policies()` — `internal_registry.py:147-152`

```python
async def get_internal_tool_policies() -> dict[str, MCPToolPolicy]:
    try:
        return await MCPStorage().list_tool_policies(INTERNAL_MCP_SERVER_NAME)
    except Exception:
        return {}
```

`INTERNAL_MCP_SERVER_NAME = "kunxiaozhi_internal"` (line 25). Any exception → empty dict (all tools allowed).

## 3. `MCPStorage.list_tool_policies(server_name)` — `src/infra/mcp/storage.py:603-613`

```python
async def list_tool_policies(self, server_name: str) -> dict[str, MCPToolPolicy]:
    collection = self._get_tool_policies_collection()
    policies: dict[str, MCPToolPolicy] = {}
    async for doc in collection.find({"server_name": server_name}).limit(
        MCP_TOOL_POLICY_LIST_LIMIT
    ):
        policy = self._doc_to_tool_policy(doc)
        if policy.tool_name:
            policies[policy.tool_name] = policy
    return policies
```

Returns a dict keyed by `tool_name`. Only tools that have an explicit policy doc in Mongo appear; everything else has `policy=None` → allowed.

## 4. `MCPToolPolicy` schema — `src/kernel/schemas/mcp.py:52-68`

```python
class MCPToolPolicy(BaseModel):
    server_name: Optional[str] = Field(None)
    tool_name: Optional[str] = Field(None)
    disabled: bool = Field(False, description="Whether this tool is disabled globally")
    allowed_roles: list[str] = Field(default_factory=list, description="Empty list = all roles.")
    role_quotas: dict[str, MCPRoleQuota] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None
```

## 5. `_policy_for_tool` + `_is_tool_allowed` — `internal_registry.py:58-80`

```python
def _policy_for_tool(policies, tool_name) -> MCPToolPolicy | None:
    policy = policies.get(tool_name)
    return policy if policy is not None else None

def _is_tool_allowed(*, policy, user_roles, is_admin) -> bool:
    if is_admin:
        return True
    if policy is None:
        return True
    if policy.disabled:
        return False
    if not policy.allowed_roles:
        return True
    return bool(set(user_roles or []).intersection(policy.allowed_roles))
```

Decision table (non-admin):

| `policy` | `policy.disabled` | `policy.allowed_roles` | Result |
|---|---|---|---|
| `None` (no doc) | — | — | **allowed** |
| set | `True` | — | **blocked** |
| set | `False` | `[]` (empty) | **allowed** |
| set | `False` | `[r1,r2]` | allowed iff user has one of those roles |

Admin (`is_admin=True`) always allowed, ignoring policy.

## 6. `get_internal_tools_for_user` — `internal_registry.py:155-184`

```python
async def get_internal_tools_for_user(*, user_id, user_roles, is_admin) -> list[BaseTool]:
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
```

Each surviving tool is wrapped in `MCPToolWithRetry` carrying `role_quotas` from the policy (or `None`).

## 7. UI toggle → DB write

The MCP UI admin toggle endpoint is `src/api/routes/mcp.py:693-726`:

```python
@admin_router.patch("/{name}/tools/{tool_name}", response_model=MCPToolToggleResponse)
async def admin_toggle_tool(name, tool_name, data: MCPToolToggleRequest, ...):
    if _is_internal_server(name):
        await storage.set_tool_policy(
            server_name=name,
            tool_name=tool_name,
            disabled=not data.enabled,
            updated_by=user.sub,
        )
    else:
        await storage.set_system_tool_disabled(name, tool_name, not data.enabled)
    ...
```

`_is_internal_server(name)` (mcp.py:70-73) returns `name == INTERNAL_MCP_SERVER_NAME` ("kunxiaozhi_internal"). So toggling a tool on the `kunxiaozhi_internal` virtual server writes a `MCPToolPolicy` doc with `disabled=True/False`.

A separate role/quota policy endpoint at `mcp.py:729-750` (`PUT /{name}/tools/{tool_name}/policy`) calls `set_tool_policy` with `allowed_roles` and `role_quotas`.

## 8. `MCPStorage.set_tool_policy` — `src/infra/mcp/storage.py:551-593`

```python
async def set_tool_policy(self, *, server_name, tool_name,
                          allowed_roles=None, role_quotas=None,
                          disabled=None, updated_by=None) -> MCPToolPolicy:
    collection = self._get_tool_policies_collection()
    existing = await collection.find_one({"server_name": server_name, "tool_name": tool_name})
    now = utc_now_iso()
    update_data = {"server_name": server_name, "tool_name": tool_name,
                   "updated_at": now, "updated_by": updated_by}
    if not existing:
        update_data["created_at"] = now
    if allowed_roles is not None:
        update_data["allowed_roles"] = allowed_roles
    if role_quotas is not None:
        update_data["role_quotas"] = {...}
    if disabled is not None:
        update_data["disabled"] = disabled
    await collection.update_one(
        {"server_name": server_name, "tool_name": tool_name},
        {"$set": update_data},
        upsert=True,
    )
    await self._call_optional_async(self._invalidate_all_cache())
    return await self.get_tool_policy(server_name, tool_name) or MCPToolPolicy(**update_data)
```

Upserts a doc in the tool-policies collection keyed by `(server_name, tool_name)`. The toggle endpoint only passes `disabled` (leaves `allowed_roles`/`role_quotas` untouched because they are `None`).

## 9. `MCPToolWithRetry` wrapping vs raw `@tool` — `src/infra/tool/mcp_client.py:62-111`

```python
class MCPToolWithRetry(BaseTool):
    _original_tool: BaseTool = PrivateAttr()
    _max_retries: int = PrivateAttr(default=MCP_MAX_RETRIES)
    _retry_delay: float = PrivateAttr(default=MCP_RETRY_DELAY)
    _user_id: str | None = PrivateAttr(default=None)
    _server_name: str | None = PrivateAttr(default=None)
    _user_roles: list[str] = PrivateAttr(default_factory=list)
    _is_admin: bool = PrivateAttr(default=False)
    _role_quotas: dict[str, MCPRoleQuota] = PrivateAttr(default_factory=dict)
    _quota_tool_name: str | None = PrivateAttr(default=None)

    def __init__(self, original_tool, max_retries=..., retry_delay=...,
                 user_id=None, server_name=None, user_roles=None,
                 is_admin=False, role_quotas=None, quota_tool_name=None):
        super().__init__(name=original_tool.name, description=original_tool.description,
                         args_schema=original_tool.args_schema)
        self._original_tool = original_tool
        ...
```

Differences from a raw `@tool`:

| Aspect | Raw `@tool` (Path 2) | `MCPToolWithRetry` (Path 1) |
|---|---|---|
| Retry on transient errors (429/503/timeout/network) | No | Yes (`_max_retries`, `_retry_delay`; `_is_retryable_error` at mcp_client.py:113) |
| Carries `user_id` / `server_name` / `user_roles` / `is_admin` | No | Yes (private attrs) |
| Per-role quota enforcement (`role_quotas`, `quota_tool_name`) | No | Yes (used by `MCPQuotaMiddleware`) |
| `server` attribute set on the tool | No | Yes (`object.__setattr__(self, "server", server_name)` at line 111) — used by quota middleware to attribute usage |
| Policy filter applied before insertion | No | Yes (`_is_tool_allowed`) |

So Path 2's raw tools not only bypass the disabled/role policy — they also skip retry wrapping and per-role quota attribution. An `env_var_set` invoked via a Path-2 raw tool would not count against any role quota.

## 10. `resolve_user_mcp_access` — `src/infra/mcp/quota.py:214-234`

```python
async def resolve_user_mcp_access(user_id: str) -> tuple[list[str], bool]:
    """Resolve user's role names and whether they have MCP admin permission."""
    user = await UserStorage().get_by_id(user_id)
    if not user or not user.roles:
        return [], False
    roles = await RoleStorage().get_by_names(user.roles)
    resolved_roles, permissions = [], set()
    for role in roles:
        resolved_roles.append(role.name)
        for permission in role.permissions:
            permissions.add(permission if isinstance(permission, str) else permission.value)
    return resolved_roles, "mcp:admin" in permissions
```

Returns `(role_names, is_admin)` where `is_admin` = user has the `mcp:admin` permission. In context.setup() (e.g. fast_agent/context.py:185-187), when `self.user_id` is falsy it short-circuits to `([], False)` — so anonymous/no-user sessions get no admin bypass and `user_roles=[]`.

## Caveats / Not Found

- `MCP_MAX_RETRIES` / `MCP_RETRY_DELAY` constants are imported at the top of `mcp_client.py` (not shown in the excerpt); the wrapper defaults to them.
- The toggle endpoint writes ONLY `disabled` for the internal server; `allowed_roles`/`role_quotas` come only from the separate `PUT .../policy` endpoint.
- `_doc_to_tool_policy` (the Mongo doc → `MCPToolPolicy` converter) was not read in full; it's referenced at storage.py:601, 610, 628.
