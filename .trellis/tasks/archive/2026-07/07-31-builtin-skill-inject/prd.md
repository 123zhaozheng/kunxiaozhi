# Child B — 内置 Skill:角色级自动注入 + 用户不可见(zip/商城来源)

> Parent: `07-31-builtin-role-injection`。共享决策见 parent `prd.md`。

## Goal

新增 **builtin skill** 存储与注入通道。admin 通过管理界面创建内置技能,支持两种内容来源:**zip 上传**或**从商城加载**;按角色自动注入匹配用户的 `get_effective_skills`,对终端用户完全不可见。

## 用户故事

- **admin**:在管理界面"内置技能"页,点"新建",选择来源:
  - **zip 上传**:上传一个 zip(复用现有 `skill_uploads` 解析,支持一个 zip 内多个 skill),预览后确认。
  - **从商城加载**:从商城列表选一个 skill,复制为内置。
  - 绑定"允许角色"(多选),保存。
- **匹配角色的普通用户**:聊天时其 agent 上下文中**自动包含**该内置 skill 内容;用户在技能列表、商城、可见技能清单**看不到**它。
- **非匹配角色用户**:不注入。

## 需求

1. **存储**:新增 builtin skill 存储(元数据 + 文件),独立于 user skill 与 marketplace skill。元数据含:`skill_name`、`description`、`allowed_roles`、`source`(`zip`/`marketplace`)、`source_ref`、`is_active`、`created_by`、时间戳。
2. **创建来源 A — zip 上传**:复用 `skill_uploads._parse_zip_skills` 解析逻辑,目标改为 builtin 存储;保留预览(`_parse_zip_skill_preview`)与上传两个步骤;尊重现有 zip 上传限制(`_sync_zip_upload_limits`)。
3. **创建来源 B — 从商城加载**:复用 `install_marketplace_skill` 中 `_copy_marketplace_files_to_user_skill` 的复制思路,源为 marketplace、目标为 builtin。
4. **注入**:`get_effective_skills(user_id)` 扩展——在现有 user skill 之外,合并 `allowed_roles` 匹配用户的 builtin skills(角色过滤、`is_active` 过滤、`disabled_skills` 过滤),加载其文件,合并进返回结果。
5. **可见性**:
   - 用户技能列表 API(`list_user_skills` 等)不含 builtin。
   - marketplace 列表不含 builtin。
   - agent 可见技能清单(若前端展示 effective skills)不暴露 builtin 标记。
   - admin 管理 API 可见全部 builtin。
6. **配额**:注入受现有 `SKILL_EFFECTIVE_LOAD_LIMIT` 与单 skill 文件上限约束;**user skill 优先**,builtin 填充剩余配额(避免挤占用户显式技能)。
7. **缓存**:builtin 创建/更新/删除失效受影响用户的 skills redis 缓存(策略见 design,推荐 MVP 粗粒度失效)。
8. **权限**:管理端点挂 `require_permissions("manage_builtin_skills")`。

## 验收标准

- [ ] admin 可通过 zip 上传创建一个 builtin skill 并绑定角色 X(含预览步骤)。
- [ ] admin 可从商城选择一个 skill 加载为 builtin 并绑定角色 X。
- [ ] 角色 X 用户发起对话,agent 上下文包含该 builtin skill 内容(测试可断言 `get_effective_skills` 返回或 middleware 注入)。
- [ ] 非 X 角色用户 `get_effective_skills` 不含该 builtin。
- [ ] 普通 X 用户在前端技能列表、商城、可见技能清单均看不到该 builtin。
- [ ] admin 可编辑(改角色 / 描述 / 启停)、删除 builtin skill。
- [ ] 现有用户 skill / marketplace skill 行为不变(回归)。

## 非目标

- 不做 builtin skill 的版本管理 / 多版本共存。
- 不做"从某用户的私有 skill 提升为 builtin"(仅 zip 与商城两个来源)。
- 不改变现有 skill 文件解析 / persona overlay / middleware 注入机制,仅在数据来源层扩展。

## Notes

- zip 解析(`skill_uploads`)与商城复制(`_copy_marketplace_files_to_user_skill`)均已存在(目标=user);本 child 主要是"换目标到 builtin 存储 + 加角色注入 + 加可见性过滤"。
- `get_effective_skills` 是注入唯一入口;前端展示列表与之是不同 API,builtin 只注入不展示。
