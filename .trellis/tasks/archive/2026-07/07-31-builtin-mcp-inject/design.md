# Child A — 技术设计:内置 MCP 工具自动注入

## 设计原则

复用现有 system server 的存储 / 加密 / 路由骨架,**只新增一个标志位 + 注入分支 + 可见性过滤**。不新建表、不新建传输协议。

## 1. Schema 扩展(`src/kernel/schemas/mcp.py`)

| 类 | 改动 |
|---|---|
| `SystemMCPServer` | 新增 `auto_inject: bool = Field(False, description="若为 True,跳过用户 preference,按 allowed_roles 自动注入且对用户不可见")` |
| `MCPServerCreate` | 新增 `auto_inject: bool = False`。普通用户创建 user server 时**忽略并拒绝**该字段(仅 admin 路由生效) |
| `MCPServerResponse` | 新增 `auto_inject: bool = False` |
| `MCPServerUpdate` | 新增 `auto_inject: Optional[bool] = None`(仅 admin 可改) |

> 与 `is_internal` 区分:`is_internal=True` 专指 `kunxiaozhi_internal` 代码虚拟服务器;`auto_inject=True` 指 DB 驱动的管理员下发服务器。二者正交。

## 2. 存储层(`src/infra/mcp/storage.py`、`storage_operations.py`)

- `create_system_server`:透传 `auto_inject` 写入 doc。
- `_doc_to_system_server` / `_doc_to_system_server_async`:回读 `auto_inject`(缺失按 False)。
- `update_system_server`:支持更新 `auto_inject`。
- 新增内部判断函数 `is_auto_inject(doc) -> bool`,集中复用。

## 3. 注入逻辑(核心,`src/infra/tool/mcp_client.py`)

**注入链**:`FastAgentContext._lazy_load_mcp_tools` → `get_global_mcp_tools(user_id)` → `MCPClientManager(use_database=True).get_tools()` → 内部加载 user + system servers。

**改动语义**(在 `get_tools()` 的 system servers 遍历分支):
- `auto_inject=False`:维持现状(受 user preference + `allowed_roles`)。
- `auto_inject=True`:**跳过 user preference 检查**(无需 enabled 偏好即可纳入候选);再经:
  - `_can_access_system_server(allowed_roles, user_roles, is_admin)` 角色过滤(admin 全过);
  - `disabled_tools` 过滤;
  - quota 包装(复用 `MCPToolWithRetry` 的 role_quotas 机制)。

> ⚠️ **待 implement 阶段确认**:`get_tools()` 当前对 system server 是否"未配置 preference 即视为启用"。
> - 若是 → `auto_inject` 的差异主要在"屏蔽用户 toggle 关闭" + "list 隐藏",注入分支改动很小。
> - 若否(必须用户启用)→ 需在加载分支显式为 `auto_inject=True` 放行。
>
> 实现时先读 `MCPClientManager.get_tools()` 与 `_apply_user_preferences` 的 system server 分支源码再下笔。

**缓存键**:沿用现有 per-user 全局缓存键(`get_global_mcp_tools` 已按 `user_id` 缓存),注入结果依赖用户角色,角色变化走现有用户 metadata 更新通道即可,无需新键。

## 4. 可见性 / 操控边界(`src/api/routes/mcp.py`)

- `list_servers`:对非 admin 过滤掉 `auto_inject=True`。
- `admin_list_servers` / 新增 `admin_list_builtin_servers`:返回全部 / 仅 `auto_inject=True`。
- `get_server`:非 admin 访问 `auto_inject=True` 的服务器 → 404(不存在语义,避免信息泄露)。
- `toggle_server` / `promote_server` / `demote_server`:检测 `auto_inject=True` 且非 admin → 403。
- `can_access_server`:保持按 `allowed_roles` 放行(供 agent 注入使用);可见性由 list 过滤独立保证。

## 5. API 路由(推荐新增,避免与普通 system server 校验耦合)

- `POST /admin/mcp/builtin`(或 `/admin/mcp/servers?builtin=true`):`admin_create_builtin_server`,挂 `require_permissions("manage_builtin_tools")`,强制 `auto_inject=True`。
- `GET /admin/mcp/builtin`:`admin_list_builtin_servers`。
- 复用 `admin_update_server` / `admin_delete_server`,允许操作 `auto_inject=True` 记录。

## 6. 前端

| 文件 | 改动 |
|---|---|
| `frontend/src/components/mcp/MCPServerToolsSidebar.tsx` | 右上角"+"按钮,`isAdmin` 时渲染;点击打开现有"添加服务器"表单;表单追加"允许角色"多选(数据源:角色 API),提交带 `auto_inject=true` |
| `frontend/src/types/mcp.ts` | `MCPServer` 类型加 `auto_inject?: boolean` |
| `frontend/src/services/api/mcp.ts`(若存在) | 新增 `createBuiltinServer` / `listBuiltinServers` |

> 前端"添加服务器"表单组件应已存在(外部添加服务器入口);本 child 复用并参数化,不重写。

## 7. 权限

- `src/infra/auth/rbac.py` `get_default_roles`:admin 角色追加 `manage_builtin_tools`。
- 新 permission 常量沿用现有命名约定集中放置。

## 兼容性 / 回滚

- `auto_inject` 默认 False → 现有数据与行为不变;MongoDB 无 schema 迁移,旧 doc 缺字段按 False。
- 回滚:移除前端"+"入口 + admin 路由;后端字段保留无害(默认 False)。

## 验证策略

- `tests/test_mcp_role_access.py` 扩展:auto_inject 服务器对匹配角色注入、对非匹配不注入、对普通用户不可见。
- `tests/api/test_mcp_routes.py`:`admin_create_builtin_server` 成功;普通用户 `list_servers` 不含 auto_inject 项;非 admin `toggle` 返回 403。
- 回归:现有 `test_mcp_role_quota.py` / `test_mcp_tool_policies.py` 全绿。
