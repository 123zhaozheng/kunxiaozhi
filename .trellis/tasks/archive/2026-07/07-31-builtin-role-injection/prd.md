# 内置工具与技能:角色级自动注入(管理员下发,用户不可见)

## Goal

让管理员(admin)能通过 UI 下发两类"内置"能力,按角色**自动注入**到匹配用户的聊天 agent 中,且对终端用户**完全不可见、不可操控**:

1. **内置 MCP 工具**(child A `07-31-builtin-mcp-inject`):在内置工具卡片详情页右上角"+"复用"添加服务器"表单,创建角色绑定的 MCP 服务器,自动注入匹配角色用户的 agent。
2. **内置 Skill**(child B `07-31-builtin-skill-inject`):admin 通过 UI 录入(**zip 上传**或**从商城加载**),按角色自动注入,对用户完全隐形。

## Background 与现状缺口(调研结论)

| 现有能力 | 位置 | 缺口 |
|---|---|---|
| System MCP Server | `src/infra/mcp/storage.py` `SystemMCPServer`,已有 `allowed_roles` | 但是"用户可见 + 用户 preference 启停"(`toggle_server`),**非自动注入** |
| `kunxiaozhi_internal` 虚拟内置服务器 | `src/infra/tool/internal_registry.py` | 工具**代码硬编码**,无法经 UI 增加 |
| Skill 生效来源 | `src/infra/skill/storage.py` `get_effective_skills(user_id)` | 仅 `user_id` scope(用户自建)+ 用户主动安装的 marketplace,**无"管理员下发、角色级、自动注入、不可见"通道** |
| zip 上传 skill / 商城安装 | `routes/skill.py` `upload_skill_from_zip`、`routes/marketplace.py` `install_marketplace_skill` | 目标都是**用户账户**,无"builtin"目标 |
| RBAC | `src/infra/auth/rbac.py`、`routes/role.py` | 已有 default roles + DB 自定义角色 + `require_permissions`,可直接复用 |

## 任务图

- **Parent(本文件)**:跨 child 共享约束、集成验收标准,不做直接实现。
- **child A** `07-31-builtin-mcp-inject`:扩展 `SystemMCPServer` 支持 `auto_inject` + 用户不可见。
- **child B** `07-31-builtin-skill-inject`:新增 builtin skill 存储 + 注入 + zip/商城两种创建来源。

A、B 互相独立,可分 PR 交付;无硬依赖,不必互相等待。

## 共享设计决策(两个 child 共遵)

| 项 | 决策 |
|---|---|
| **可见性** | 两类内置能力对终端用户**完全不可见**:不出现在用户侧任何列表/卡片/商城/可见技能清单。仅 admin 在管理界面可见可管。 |
| **角色数据源** | 复用 RBAC:`RBACManager.get_default_roles()` + DB `role` 表自定义角色;前端下拉**多选**。 |
| **角色匹配** | `allowed_roles` 为空 → 对所有角色生效;非空 → 用户 roles 与之有交集即注入。admin 始终可见可用。无角色用户的处理在各 child design 中明确。 |
| **权限校验** | 复用 `require_permissions`,新增两个 permission key:`manage_builtin_tools`(A)、`manage_builtin_skills`(B),挂到 admin 默认角色。 |
| **自动注入语义** | 不写用户 preference、不允许用户 toggle;在 agent 工具/skill 加载路径按角色强制纳入。 |
| **缓存失效** | 创建/更新/删除内置项需失效受影响用户的 MCP 全局缓存(`mcp_global` 失效通道)与 skills redis 缓存(`invalidate_user_cache`)。 |

## 跨 child 集成验收标准

- [ ] admin 在管理界面可创建/编辑/删除内置 MCP 工具与内置 Skill,均可指定角色。
- [ ] 匹配角色的普通用户发起对话时,agent 实际加载到内置 MCP 工具与内置 Skill(日志/trace 可证)。
- [ ] 普通用户在前端任何入口(MCP 列表、工具面板、技能列表、商城、可见技能清单)均看不到内置项。
- [ ] 非匹配角色用户与匿名用户不被注入。
- [ ] admin 在管理界面可查看所有内置项及其角色绑定与来源。

## 非目标

- 不改变现有 `kunxiaozhi_internal` 虚拟服务器的代码硬编码工具集。
- 不做内置项的版本管理 / 灰度发布 / 审计日志(后续任务)。
- 不做"按单个用户(而非角色)粒度"的下发。
- 不引入新的 MCP 传输协议(复用现有 SSE / STREAMABLE_HTTP / SANDBOX)。
- 不做内置 Skill 的运行时沙箱隔离增强(沿用现有 skill 加载与裁剪机制)。

## Notes

- 本 Parent 仅承载需求与集成 AC;具体技术方案见各 child 的 `design.md`。
- 实现顺序建议:child A 与 child B 任一先做均可;若资源受限,优先 child B(缺口更大、价值更直接)。
