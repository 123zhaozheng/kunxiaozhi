# Child A — 执行计划

## 前置确认(implement 第一件事)

读 `src/infra/tool/mcp_client.py` 中 `MCPClientManager.get_tools()` 与 system server 加载分支(含 `_apply_user_preferences` 调用点),记录:
- system server 默认是否需要 user preference 才启用?
- `allowed_roles` 在哪一层过滤?

据结论定 design.md §3 的注入分支具体改法。

## 有序 Checklist

1. **[schema]** `src/kernel/schemas/mcp.py`:为 `SystemMCPServer` / `MCPServerCreate` / `MCPServerResponse` / `MCPServerUpdate` 加 `auto_inject`。
   → verify: `uv run python -c "from src.kernel.schemas.mcp import SystemMCPServer; print('auto_inject' in SystemMCPServer.model_fields)"`
2. **[storage]** `storage.py` / `storage_operations.py`:`create_system_server` / `_doc_to_system_server*` / `update_system_server` 透传 `auto_inject`;加 `is_auto_inject(doc)`。
   → verify: 单测 create_system_server 写入并回读 `auto_inject=True`。
3. **[注入]** `mcp_client.py` `get_tools()`:按前置确认结论,为 `auto_inject=True` 放行(跳过 preference),保留 `allowed_roles` / `disabled_tools` / quota 过滤。
   → verify: 注入单测(匹配角色加载、非匹配不加载)。
4. **[可见性]** `routes/mcp.py` `list_servers` 过滤 `auto_inject` 对非 admin;`get_server` 对非 admin 返回 404。
   → verify: 路由测试。
5. **[操控拦截]** `toggle_server` / `promote_server` / `demote_server`:对 `auto_inject=True` 且非 admin 返回 403。
   → verify: 路由测试。
6. **[admin 路由]** 新增 `admin_create_builtin_server` / `admin_list_builtin_servers`,挂 `require_permissions("manage_builtin_tools")`;扩展 `admin_update_server` / `admin_delete_server` 支持 auto_inject 记录。
   → verify: 权限 + 创建测试。
7. **[权限]** `src/infra/auth/rbac.py` `get_default_roles`:admin 角色追加 `manage_builtin_tools`。
   → verify: default roles 含该 key。
8. **[前端]** `MCPServerToolsSidebar.tsx` 右上角"+"(admin);复用"添加服务器"表单 + 角色多选;`types/mcp.ts` 加 `auto_inject`;`services/api/mcp.ts` 加调用。
   → verify: 组件渲染测试 + 手动验证普通用户看不到"+"。
9. **[测试]** 扩展 `tests/test_mcp_role_access.py`、`tests/api/test_mcp_routes.py`;跑回归 `tests/test_mcp_role_quota.py`、`tests/test_mcp_tool_policies.py`。
   → verify: `uv run pytest tests/test_mcp_role_access.py tests/api/test_mcp_routes.py tests/test_mcp_role_quota.py tests/test_mcp_tool_policies.py -q`

## Review Gates(暂停等 review)

- 步骤 3 完成后:确认注入逻辑与现有 preference 语义不冲突、无越权。
- 步骤 8 完成后:确认普通用户视角确实看不到入口与服务器。

## Rollback Points

- 步骤 1–2:纯加字段,可保留无害。
- 步骤 3–6:行为变更,可通过回退代码或 `auto_inject` 默认 False 快速回滚(无数据存在时等同于功能关闭)。
- 步骤 7–8:权限与 UI,可独立回退。
