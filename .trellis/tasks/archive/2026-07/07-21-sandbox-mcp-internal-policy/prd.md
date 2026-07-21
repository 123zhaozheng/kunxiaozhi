# sandbox_mcp 管理工具并入 internal 统一管控

## Goal

让 `sandbox_mcp_add` / `sandbox_mcp_update` / `sandbox_mcp_remove` 与 `create_agent_team`、`env_var_*` 等内置工具一样，走 `kunxiaozhi_internal` + `MCPToolPolicy` 统一管控，使管理员在 MCP UI 的内置服务工具列表中可禁用 / 配角色 / 配额。

## Background

- 现状：三件套只在 Agent context（`ENABLE_SANDBOX`）硬挂，不进 `build_internal_tools()`，UI 内置策略摸不到。
- `BUILTIN_TOOLS` 还把它们标成用户不可禁用，与策略管控冲突。
- **不在范围**：sandbox 传输模式 UI、自定义 npx/stdio MCP、`mcporter call` 细粒度工具管控。

## Requirements

1. `ENABLE_SANDBOX=True` 时，`build_internal_tools()` 包含 `sandbox_mcp_add` / `update` / `remove`。
2. `ENABLE_SANDBOX=False` 时，不暴露这三件套。
3. 三件套经 `get_internal_tools_for_user()` 应用 `kunxiaozhi_internal` 的 tool policy（`disabled` / `allowed_roles` / `role_quotas`），与其他 internal tools 一致（含 `MCPToolWithRetry` 包装）。
4. `SearchAgentContext` / `FastAgentContext` 不再单独 `extend(get_sandbox_mcp_tools())`，只通过 internal tools 注入，避免双挂。
5. 从 `BUILTIN_TOOLS` 移除 `sandbox_mcp_*`，使会话级 / 策略级禁用生效。
6. 有单测覆盖：registry 在 sandbox on/off 下的工具集合；policy 禁用后 `get_internal_tools_for_user` 不返回该工具；context 不重复挂载（若有现成测试则对齐更新）。

## Out of Scope

- UI 增加 sandbox 传输模式
- 沙箱内自定义 MCP server / tool 的 discover 与 per-tool 管控
- `mcporter call` 中间件策略扩展
- 改动 `sandbox_mcp_tool` 业务逻辑（持久化 / mcporter CLI 调用）本身

## Acceptance Criteria

- [x] `build_internal_tools()` 在 `ENABLE_SANDBOX` 时返回三件套，关闭时不返回
- [x] 三件套出现在 internal tool infos（可供 MCP UI / admin policy）
- [x] policy `disabled=true` 时，对应用户拿不到该 tool
- [x] Fast / Search context setup 不再直接调用 `get_sandbox_mcp_tools`
- [x] `BUILTIN_TOOLS` 不含 `sandbox_mcp_*`
- [x] 相关单测通过

## Notes

- Lightweight：PRD 足够；实现改动面小（registry + 两 context + tool_filter + tests）。
