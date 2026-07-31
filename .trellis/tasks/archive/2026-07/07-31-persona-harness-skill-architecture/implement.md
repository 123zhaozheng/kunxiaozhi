# Persona Harness Skill 与 Builtin 只读 Skill 实施计划

## 阶段 0：建立安全基线与精确回撤

- [ ] 保存当前 `git status` 与 scoped diff，标记 Persona materialization、Builtin、其他并行改动的归属。
- [ ] 恢复 `src/api/routes/marketplace.py` 原手动安装实现，删除仅服务错误方案的 `src/infra/skill/installation.py` 及对应测试。
- [ ] 删除前后端 `create_request_id`、Mongo partial unique index和错误的重复创建测试。
- [ ] 保留 Persona overlay 已移除的正确方向，不恢复双轨 overlay。

验证：

```powershell
rg -n "create_request_id|ensure_marketplace_skill_installed|copy_marketplace_skill_to_user" src frontend/src tests
uv run pytest tests/api/test_marketplace_routes.py tests/infra/skill/test_marketplace_storage.py -q
```

## 阶段 1：Persona schema、保存校验与直接更新

- [ ] Persona agent 类型收敛为 Fast/Search；新增完整对象 agent/skill 约束校验。
- [ ] 删除 Persona `marketplace_skills`、publication preflight、`publish_personal_skills` 契约；新增 runtime `PersonaSkillHint`。
- [ ] `MarketplaceStorage` 增加按名称批量读取 active metadata 与 `SKILL.md` 内容的方法，保序、去重、限制数量。
- [ ] Persona create/update 在合并后的完整对象上校验所有 Marketplace 名称；不发布任何 Skill。
- [ ] 删除 Persona publication route、前端 API/types 与 manager 补偿逻辑。
- [ ] 锁定“编辑个人 Persona → 官方已发布”为一次 PUT、同一 id 原地更新。

验证：

```powershell
uv run pytest tests/persona_preset tests/api/test_persona_preset_routes.py tests/infra/skill/test_marketplace_storage.py -q
```

关键测试：

- create/update 带 Skill + Fast 被拒绝；Search 成功。
- Team schema 请求被拒绝。
- Skill 缺失/inactive 保存被拒绝；多名称批量查询而非 N+1。
- personal→global 的 storage id 不变，create 方法零调用。

## 阶段 2：Persona 编辑器改造

- [ ] Marketplace list API 支持严格 `active_only=true`，不包含发布者自己的 inactive 项。
- [ ] Persona picker 改用 Marketplace 分页/搜索结果，删除 `useSkills` 个人 Skill 依赖。
- [ ] 选中任意 Skill 自动切 Search 并禁用 Fast；清空后恢复 Fast 可选。
- [ ] Team 选项和类型彻底移除。
- [ ] 删除 Skill 发布确认弹窗、preflight、loading/ref、幂等 request id。
- [ ] 更新中英文 i18n 与相关 UI/type 测试。

验证：

```powershell
pnpm --dir frontend type-check
pnpm --dir frontend test -- PersonaEditorModal personaPresetEditor marketplace
```

## 阶段 3：Persona 激活与 Search Agent Harness

- [ ] `use_preset` 删除所有用户 Skill 实体化与可用性阻断，批量读取 active Marketplace `SKILL.md` 并解析最新 frontmatter description。
- [ ] 缺失/inactive/空 description 仅记录日志并省略 hint。
- [ ] 新增 `persona_skill_hints` runtime helper，并接入 Web、WeCom、session queue/recovery。
- [ ] `resolve_persona_request` 停止覆盖 `enabled_skills`。
- [ ] 新增 Harness 纯函数：仅 `install_skill` 最终可用且 Sandbox 已建立时注入。
- [ ] Search 主 Agent 与 subagent 同时注入；Fast/Team 不注入。
- [ ] 保持现有 `install_skill` 代码与行为不变。

验证：

```powershell
uv run pytest tests/agents/test_persona_preset_runtime.py tests/persona_preset/test_manager.py tests/infra/agent/wecom tests/infra/tool/test_skill_marketplace_tool.py tests/infra/task -q
```

关键测试：

- 多 Skill prompt 包含精确 name + 最新 description，不包含预安装或 Persona 专属 find 指令。
- `install_skill` 缺失、策略禁用、Sandbox 关闭时 section 为空。
- Persona 激活不写 SkillStorage、不修改 enabled_skills，仍正常返回 snapshot。
- Marketplace 更新 `SKILL.md` description 后新激活 snapshot/harness 立即变化。

## 阶段 4：Builtin 物理存储与逻辑投影

- [ ] 完善 Builtin metadata（tags、内容更新时间）及批量角色/名称/文件查询。
- [ ] 新增 `EffectiveSkillStorage`，集中实现 user-first source resolution。
- [ ] 将 Builtin 合并和 cache version 从 `SkillStorage.get_effective_skills` 迁出，恢复 `SkillStorage` 的纯物理职责。
- [ ] `SkillManager`、loader 和 middleware 改用逻辑投影。
- [ ] user disabled 项仍遮蔽同名 Builtin；Builtin 只占剩余额度。

验证：

```powershell
uv run pytest tests/infra/skill/test_builtin_storage.py tests/infra/skill/test_effective_skill_storage.py tests/infra/skill/test_loader_prompt.py -q
```

关键测试矩阵：user-only、builtin-only、同名 user、同名 user disabled、角色不匹配、Builtin inactive、配额边界、admin 更新版本缓存。

## 阶段 5：用户 Skills API 与偏好隔离

- [ ] list/detail/file read 接入 `EffectiveSkillStorage`；返回 `source` 与 `read_only`。
- [ ] 合并两来源的搜索、tag、排序、分页、total/enabled_count。
- [ ] 新增三组 Builtin metadata 偏好 key，并扩展 profile metadata 限额校验。
- [ ] toggle/preference 根据当前有效来源写入 user 或 Builtin key。
- [ ] 用户 Skill 删除只清理 user 偏好；Builtin 偏好持续保留。
- [ ] Builtin-only 的文件写/上传/删除、Skill 删除/发布在后端拒绝。
- [ ] ZIP/Marketplace 显式安装同名用户 Skill 后自然遮蔽 Builtin。

验证：

```powershell
uv run pytest tests/api/test_skill_routes.py tests/api/routes/test_auth_profile_metadata.py tests/infra/skill/test_storage_list_user_skills.py -q
```

## 阶段 6：虚拟 `/skills`、transfer 与前端只读体验

- [ ] `SkillsStoreBackend` 的 read/ls/grep/glob/batch download 全部切到逻辑读取层。
- [ ] write/edit/upload 对 Builtin 返回稳定的 read-only/permission error。
- [ ] 二进制 Builtin 由 transfer 下载真实对象字节，不返回引用 JSON。
- [ ] Skills Hub 显示 Builtin 来源/只读标记；允许查看、启停、收藏、置顶。
- [ ] Builtin 隐藏编辑、删除、重命名、上传、发布入口；后端测试仍证明伪造请求被拒绝。

验证：

```powershell
uv run pytest tests/infra/backend/test_skills_store_backend.py tests/infra/backend/test_deepagents_protocol_compat.py tests/infra/tool/test_transfer_file_tool.py -q
pnpm --dir frontend type-check
pnpm --dir frontend test -- SkillsHub SkillCard skillAvailability
```

## 阶段 7：Builtin admin 更新单一源

- [ ] admin 支持原地替换 Builtin ZIP 内容；Marketplace 来源支持按 `source_ref` 刷新。
- [ ] 更新成功后 bump Builtin version；不枚举用户、不创建 user copies。
- [ ] Builtin 管理页增加替换/刷新操作与状态反馈。
- [ ] 验证同名 user 阴影不变化，删除 user 后读取到最新 Builtin 内容和旧 Builtin 偏好。

验证：

```powershell
uv run pytest tests/api/test_builtin_skill_routes.py tests/infra/skill/test_builtin_storage.py tests/infra/skill/test_effective_skill_storage.py -q
pnpm --dir frontend test -- builtinSkill BuiltinSkillsPanel
```

## 阶段 8：规范、残留扫描与全链路质量门

- [ ] 更新 Persona Marketplace、Preferred Agent、Marketplace Sandbox 和 Builtin 投影 Trellis specs。
- [ ] 删除/重写错误方案测试与过时文案。
- [ ] 扫描所有旧 runtime 字段和 Persona publication orchestration 残留。
- [ ] 执行 Python lint/type/test 与前端 lint/type/test。

残留扫描：

```powershell
rg -n "create_request_id|publish_personal_skills|skill-publication/preflight|persona_marketplace_skills|ensure_marketplace_skill_installed|PersonaSkillStorageOverlay" src frontend/src tests .trellis/spec
rg -n 'preferred_agent_id.*team|"fast"\s*,\s*"search"\s*,\s*"team"' src frontend/src tests
```

最终验证：

```powershell
uv run ruff check src tests
uv run mypy src
uv run pytest tests/persona_preset tests/api/test_persona_preset_routes.py tests/api/test_skill_routes.py tests/api/test_builtin_skill_routes.py tests/api/test_marketplace_routes.py tests/infra/skill tests/infra/backend tests/infra/tool/test_transfer_file_tool.py tests/infra/tool/test_skill_marketplace_tool.py tests/agents -q
pnpm --dir frontend lint
pnpm --dir frontend type-check
pnpm --dir frontend test
```

## Review Gates

1. 阶段 1–3 后先检查 Persona scoped diff：不得再有发布/永久安装/overlay/Team 残留。
2. 阶段 4–6 后检查同一个 Builtin 在 list、prompt、read、transfer 的 source resolution 完全一致。
3. 阶段 7 后检查 admin 更新没有写任何 `skill_files.user_id` 数据。
4. 最终只处理本任务触及的失败；若发现其他脏工作树改动导致失败，记录并隔离，不擅自回撤。

## 风险文件

- `src/kernel/schemas/persona_preset.py`
- `src/infra/persona_preset/manager.py`
- `src/infra/persona_preset/storage.py`
- `src/api/routes/{persona_preset,chat,marketplace,skill,builtin_skill}.py`
- `src/infra/skill/{storage,marketplace,builtin,effective,manager,loader}.py`
- `src/infra/backend/skills_store.py`
- `src/agents/search_agent/nodes.py`
- `src/infra/agent/wecom/handler.py`
- `src/infra/task/recovery.py`
- `frontend/src/components/persona/PersonaEditorModal.tsx`
- `frontend/src/components/panels/{SkillsHubPanel,BuiltinSkillsPanel}.tsx`
- `frontend/src/hooks/{useSkills,useMarketplace}.ts`
- `frontend/src/services/api/{personaPreset,marketplace,skill,builtinSkill}.ts`

## 回滚原则

- 每个阶段保持可独立验证的小步 patch。
- 不使用 `git reset` / `git checkout`；所有回撤通过明确 hunk 完成。
- 不恢复 Persona overlay，也不恢复永久实体化；若 Harness 阶段出错，安全降级为“仅 Persona system prompt”。
- Builtin 投影可通过停用 user-facing merge 回滚，系统 collection 数据保留，不删除用户数据。
