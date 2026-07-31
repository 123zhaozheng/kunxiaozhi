# Child A — 内置 MCP 工具:角色级自动注入

> Parent: `07-31-builtin-role-injection`。共享决策(可见性、角色源、权限、缓存)见 parent `prd.md`。

## Goal

扩展 `SystemMCPServer`,新增"**自动注入 + 用户不可见**"语义,使 admin 可在内置工具卡片详情页右上角"+"号、复用现有"添加 MCP 服务器"表单,创建角色绑定的内置 MCP 服务器,自动注入到匹配角色用户的 agent。

## 用户故事

- **admin**:打开内置工具卡片详情(`MCPServerToolsSidebar`),点右上角"+",弹出与"添加服务器"一致的表单(transport / url / command / env_keys / headers),并多一个"允许角色"下拉(多选)。提交后该服务器以内置分类存在。
- **匹配角色的普通用户**:无需任何操作,聊天 agent 自动加载该服务器的工具;UI 上看不到该服务器,也无法开关。
- **非匹配角色用户**:agent 不会加载该服务器。

## 需求

1. **Schema**:`SystemMCPServer` / `MCPServerCreate` / `MCPServerResponse` / `MCPServerUpdate` 新增 `auto_inject: bool`(默认 False)。与现有 `is_internal`(仅用于 `kunxiaozhi_internal` 虚拟服务器)语义区分,不复用。
2. **Admin 路由**:新增 admin 创建/列表端点(推荐 `admin_create_builtin_server` / `admin_list_builtin_servers`),接收 `auto_inject=True` + `allowed_roles`;仅持 `manage_builtin_tools` 可调用。
3. **注入**:`MCPClientManager.get_tools()` 加载 system servers 时,对 `auto_inject=True` 的服务器**跳过用户 preference 检查、强制纳入**,但仍受 `allowed_roles`、`disabled_tools`、quota 过滤。
4. **可见性**:`list_servers` 等用户侧枚举对**非 admin**隐藏 `auto_inject=True` 的服务器;`admin_list_servers` / `admin_list_builtin_servers` 可见。
5. **不可操控**:`toggle_server` / `promote_server` / `demote_server` 对 `auto_inject=True` 服务器拒绝普通用户调用(admin 可改)。
6. **前端**:`MCPServerToolsSidebar` 右上角"+"仅对 admin 可见;点击复用现有"添加服务器"表单组件,追加"允许角色"多选。
7. **缓存**:创建/更新/删除失效相关用户 MCP 全局缓存。

## 验收标准

- [ ] admin 可创建一个 `auto_inject=True` 的 MCP 服务器并绑定角色 X。
- [ ] 角色 X 的用户 agent 加载到该服务器工具(集成测试或日志验证)。
- [ ] 非 X 角色用户 agent 不加载。
- [ ] 普通 X 用户在 `/mcp` 列表与工具面板看不到该服务器,无法 toggle。
- [ ] admin 在管理界面可见并可编辑/删除。
- [ ] 现有 system server(`auto_inject=False`)行为完全不变(回归)。

## 非目标

- 不改造 `kunxiaozhi_internal` 虚拟服务器的硬编码工具。
- 不为内置 MCP 新增配额模型(现有 `role_quotas` 可复用,不在本 child 扩展)。
- 不做内置 MCP 的导入/导出(后续)。

## Notes

- 注入逻辑的现状确认(system server 默认是否需要 user preference 才启用)是本 child 最大风险点,详见 `design.md` 的"待 implement 阶段确认"。
