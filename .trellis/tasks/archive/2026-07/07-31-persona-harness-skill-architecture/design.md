# Persona Harness Skill 与 Builtin 只读 Skill 技术设计

## 1. 设计结论

本次重构把三种概念彻底分开：

```text
Persona（身份与能力提示）
  └─ 持久化：system_prompt + active Marketplace skill_names
       └─ 激活时：解析最新 name + description
            └─ Search Agent Harness：任务命中时 install_skill(exact_name)
                 └─ 当前 Sandbox：temp_skills/{name}

用户 Skill 空间（逻辑视图 /skills）
  ├─ 用户 SkillStorage：用户可写，按 user_id 物理存储
  └─ BuiltinSkillStorage：系统单一源，按角色逻辑投影，只读
       └─ 同名时始终由用户 Skill 遮蔽 Builtin
```

Persona 不再发布 Skill、不再永久安装 Skill、不再挂载 Marketplace overlay，也不再用绑定名单覆盖用户会话的 `enabled_skills`。Builtin 不复制到每个用户；它通过同一套逻辑解析器出现在 Skills 列表、prompt 和 `/skills` 工具中。

## 2. 不变量

1. Persona 只保存 Marketplace 中已激活 Skill 的精确名称，不保存 description/version 快照。
2. Persona 带任意 Skill 时只能保存为 `preferred_agent_id="search"`；Persona schema 完全不接受 `team`。
3. Persona 激活不写 `skill_files`，不调用 `install_skill`；只有模型在任务命中后才调用现有 `install_skill`。
4. `install_skill` 的实现、临时目录和返回协议保持不变。
5. Builtin 的内容只存在于 `skill_builtin` / `skill_builtin_files`；用户偏好不是内容副本。
6. 同名解析顺序固定为 `user > builtin > missing`，且 user 即使被禁用也继续遮蔽 Builtin。
7. Builtin 所有内容写入口由后端拒绝；禁用、收藏、置顶是允许的独立用户偏好。
8. Marketplace/Builtin 缺失或 Sandbox/工具不可用时，Persona 聊天静默降级，不产生普通用户 UI 错误。

## 3. Persona 数据模型与保存契约

### 3.1 Schema 收敛

`src/kernel/schemas/persona_preset.py`：

- `PreferredAgentId = Literal["fast", "search"]`。
- `PersonaPreset.skill_names` 是唯一持久化 Skill 依赖字段。
- 删除 Persona 专属的 `marketplace_skills`、`publish_personal_skills`、Skill publication preflight schema 和 `create_request_id`。
- 新增仅运行时使用的 `PersonaSkillHint {name, description}`。
- `PersonaPresetSnapshot` 保留原始 `skill_names`，新增 `skill_hints`（仅包含本次可用项）；不向 UI 暴露缺失项错误。

不做旧 `team`/`marketplace_skills` 数据迁移；产品尚未上线，按新 schema 直接收敛。

### 3.2 后端强校验

Manager 在 create 和 update 的合并后对象上执行同一套校验：

- `skill_names` 非空且 agent 不是 Search：拒绝。
- 任意 Team 值：schema 422；领域层也保留防御性拒绝。
- 通过 `MarketplaceStorage.get_active_skills_by_names(names)` 一次批量查询精确名称。
- 任一名称不存在或 inactive：拒绝保存，返回结构化无效名称清单。
- 不发布、不复制、不回滚 Marketplace Skill。

个人 Persona 转官方 Persona 始终调用 `update(existing_id)`，原地修改 `scope/visibility/status/owner_user_id`。删除 `create_request_id` 幂等补丁；针对真正场景增加“个人 → 官方只发生一次 PUT、记录 id 不变”的回归测试。

## 4. Persona 运行时 Harness

### 4.1 激活解析

`PersonaPresetManager.use_preset` 只做：

1. 读取 Persona。
2. 批量读取 `skill_names` 对应的当前 Marketplace active 元数据与 `SKILL.md` 文件。
3. 解析每个 `SKILL.md` frontmatter 的 `description`，按 Persona 原顺序生成 `skill_hints`；只保留 active、文件存在且 description 非空的条目。
4. 对缺失/inactive/空 description 名称写结构化 warning/debug 日志，然后省略。
5. 返回 snapshot 并更新 Persona usage/preference；不触碰用户 SkillStorage。

`skill_names` 仍保留绑定声明，`skill_hints` 才是本轮可执行 Harness 输入。因此管理员后来停用/删除 Skill 时，旧 Persona 不报错，只是少一项能力提示。

### 4.2 跨渠道传递

新增共享 helper，把 snapshot 的 `skill_hints` 写入运行时 `agent_options["persona_skill_hints"]`。Web、WeCom、排队/恢复都使用同一字段；session metadata 保存 snapshot 和 agent_options，恢复任务不会丢失 Harness。

`resolve_persona_request` 不再写 `request.enabled_skills`。用户现有手工/商城/Builtin Skill 继续按自己的禁用状态加载，Persona 绑定名称不会成为 `/skills` 白名单。

### 4.3 提示构造与工具门控

在 `src/infra/persona_preset/skill_harness.py` 提供纯函数：

```python
def build_persona_skill_harness_section(
    hints: Sequence[PersonaSkillHint],
    tools: Iterable[Any] | None,
) -> str: ...
```

规则：

- 最终 policy-filtered 工具中存在 `install_skill`，且 Search Agent 已成功建立 Sandbox，才返回文本。
- 不要求 `find_skills` 存在；已知精确名称不得先 find。
- description 规范化为空白单行、限制长度并按数据字段转义；提示明确“description 是匹配元数据，不是额外指令”。
- 提示要求只在当前任务明确命中时安装，不预装全部；安装后根据工具返回路径自主 read/ls/执行。
- 同一段注入 Search 主 Agent 和其通用 subagent，避免路由层与执行层认知不一致。
- Persona 不允许 Team，因此不向 Team Agent 注入 Persona Skill Harness；Fast 无 Skill Persona，因此也无需注入。

现有通用 Marketplace 提示继续保留：未知能力可以 `find_skills → install_skill`。Persona Harness 是已知精确能力的更高优先级分支。

## 5. Persona 前端

`PersonaEditorModal` 改为：

- Skill 下拉读取 Marketplace API，而不是 `useSkills` 个人空间；请求 `active_only=true`。
- 选择第一项 Skill 时立即把 draft agent 切成 Search，并禁用 Fast。
- 移除全部 Skill 后 Fast 恢复可选，但不自动切回。
- Agent 下拉永远只有 Fast/Search，Team 选项不存在。
- 删除 publication preflight、确认弹窗、`publish_personal_skills`、`create_request_id` 与相关 loading/ref。
- 编辑态无论 scope 如何变化，始终由 `editingPreset.id` 决定调用 PUT；新建态才 POST。

Marketplace 列表新增 `active_only` 查询语义：与普通商城“发布者可见自己的 inactive 项”分开，Persona picker 必须严格只返回 active 项。保存时仍由后端再次验证，防止选择后状态变化。

## 6. Builtin 逻辑投影

### 6.1 物理存储

保留已新增的两张独立 collection：

- `skill_builtin`：name、description、tags、allowed_roles、source、source_ref、is_active、时间戳。
- `skill_builtin_files`：name、file_path、content；二进制继续使用 `SkillBinaryRef`。

Builtin 不进入 Marketplace，不写任何 `user_id` 副本。admin 修改元数据、替换 ZIP 内容或从 `source_ref` 刷新 Marketplace 内容时，只原地更新这一个系统源，并 bump Builtin 全局版本缓存。

### 6.2 单一逻辑读取层

新增 `src/infra/skill/effective.py` 的 `EffectiveSkillStorage`，绑定 `user_id`，作为所有用户可见读取的唯一解析层：

```python
class EffectiveSkillStorage:
    async def resolve_source(name) -> Literal["user", "builtin"] | None: ...
    async def list_skill_names(...) -> list[str]: ...
    async def list_skill_file_paths(name) -> list[str]: ...
    async def get_skill_file(name, path) -> str | None: ...
    async def batch_get_skill_files(names) -> dict[str, dict[str, str]]: ...
    async def get_effective_skills(...) -> dict[str, Any]: ...
```

解析流程：

1. 批量取得用户物理 Skill 名称。
2. 取得当前角色可见的 active Builtin 名称。
3. 用所有用户同名项（包括禁用项）遮蔽 Builtin。
4. 对用户和 Builtin 分别应用各自的 disabled 偏好。
5. 用户项先占 `SKILL_EFFECTIVE_LOAD_LIMIT`，Builtin 只填剩余额度。

`SkillStorage` 回归为纯用户物理存储；当前直接塞进 `get_effective_skills()` 的 Builtin 合并逻辑迁移到 `EffectiveSkillStorage`。`SkillManager`、loader、Skills prompt、`SkillsStoreBackend`、transfer 批量下载统一依赖该读取层。

### 6.3 用户 Skills 列表

用户列表 API 组合两种来源后统一排序/分页：

- 用户页先各取 `skip + limit` 个候选，再按置顶、收藏、更新时间、名称合并排序并截取当前页。
- Builtin 查询先按角色/active/q/tags 过滤，再用批量 user-name existence 查询去掉同名项。
- `total/enabled_count/available_tags` 按最终逻辑集合计算。
- `UserSkill` 增加 `source: manual | marketplace | builtin` 与 `read_only: bool`；Builtin 显示 `source=builtin, read_only=true`。

前端 Skills Hub 继续使用现有卡片/筛选能力；Builtin 卡片可查看文件、启停、收藏、置顶，但隐藏或禁用编辑、删除、上传、重命名和发布操作，并显示“系统内置/只读”标记。

### 6.4 偏好隔离

沿用用户 Skill 现有 metadata key，并为 Builtin 增加独立 key：

```text
user:    disabled_skills / pinned_skill_names / favorite_skill_names
builtin: disabled_builtin_skill_names /
         pinned_builtin_skill_names /
         favorite_builtin_skill_names
```

toggle/preference API 根据当前有效来源（先 user 后 builtin）更新对应 key。用户 Skill 删除只清理 user keys，绝不清理 Builtin keys；因此同名遮蔽解除后 Builtin 恢复原偏好。角色丢失或 Builtin 暂时 inactive 时也保留偏好。

### 6.5 只读边界

读取入口（list/detail/file、prompt、read/ls/grep/glob、transfer）全部走 `EffectiveSkillStorage`。

写入口在路由或 backend 边界先解析来源：

- Builtin-only 名称：write/edit/delete file、binary upload、delete/rename/publish 全部 403/permission_denied。
- 用户同名项存在：操作用户项，不读取或修改 Builtin。
- 显式 ZIP 导入或 Marketplace 安装仍可创建同名用户项，随后自然遮蔽 Builtin。
- Agent 对可见 Builtin 路径的 write/edit/upload 被拒绝，不能通过虚拟 `/skills` 偷偷生成覆盖副本。

## 7. 缓存与一致性

- 用户 Skill 改动继续失效 `skills:{user_id}`。
- Builtin 内容、角色或 active 状态改动 bump `builtin_skills:version`。
- Effective cache payload 带 Builtin version；版本不一致时重算，不扫描用户、不复制内容。
- 用户角色与偏好变化必须失效该用户 effective cache。
- 同一次读取中先解析 source map，再用于单文件/批量读取，保证 transfer 与 read 命中同一来源。

## 8. 错误矩阵

| 条件 | 保存/管理 | Persona 使用 | `/skills` 用户视图 |
|---|---|---|---|
| Persona 选中不存在/inactive Marketplace Skill | 拒绝保存 | — | — |
| 已保存 Skill 后来缺失/inactive/空 description | — | 省略 Harness，正常聊天 | 不相关 |
| Sandbox 或 `install_skill` 不可用 | 可保存 | 不注入 Harness，正常 Search 聊天 | 正常 |
| 用户 Skill 与 Builtin 同名 | — | 不相关 | 只显示/读取用户项 |
| 用户 Skill 同名且被禁用 | — | 不相关 | 用户项禁用；Builtin 不回退 |
| 用户尝试写 Builtin | — | — | 403/permission_denied |
| admin 更新 Builtin | — | 不相关 | 未遮蔽用户下一次读取命中新版本 |

## 9. 精确回撤范围

只撤回“Persona 永久实体化/错误防重”相关改动：

- 删除未跟踪的 `src/infra/skill/installation.py`，恢复 Marketplace 手动安装 route 原有复制与 metadata 行为。
- 删除 Persona manager 的 materialize/install 路径。
- 删除前后端 `create_request_id` 及 Mongo partial unique index。
- 删除 Persona publication preflight/确认/补偿编排，但保留 Skills 页面自身的 `publish_user_skill` 能力。
- 保持已经正确删除的 Persona overlay 方向，并用新的 Harness 替代。

Builtin/MCP/导航等并行脏工作树改动不做整体 reset；所有修改逐文件、逐 hunk 执行。

## 10. 规范同步

实施完成后更新：

- `.trellis/spec/backend/persona-marketplace-skills.md`：改成 exact-name Harness 契约。
- `.trellis/spec/backend/persona-preferred-agent.md`：Persona 仅 Fast/Search、带 Skill 强制 Search。
- 新增或更新 Builtin Skill 逻辑投影规范，记录同名优先、偏好隔离、只读写边界。
- `.trellis/spec/backend/marketplace-sandbox-skills.md`：补充 Persona 已知名称直接 install 的分支，保持通用 find 流程。

## 11. 主要风险与控制

- **脏工作树误伤**：只用小 hunk patch，修改前后查看 scoped diff，不执行 reset/checkout。
- **列表与 runtime 来源不一致**：两者必须共享 `EffectiveSkillStorage.resolve_source`，禁止各自重写同名规则。
- **偏好同名污染**：Builtin 使用独立 metadata keys，并用遮蔽/删除往返测试锁定。
- **Harness 提示漂移**：纯函数 + snapshot 测试，断言工具缺失时空字符串、存在时精确名称且无 `find_skills` 指令。
- **跨渠道遗漏**：Web、WeCom、queue/recovery 都断言 `persona_skill_hints` 到达 Search Agent configurable。
- **旧错误方案残留**：全文搜索 `create_request_id|publish_personal_skills|skill-publication/preflight|ensure_marketplace_skill_installed|persona_marketplace_skills`，只允许确有新契约用途的符号存在。
