# Child B — 执行计划

## 前置确认

- 读 `src/infra/skill/marketplace.py`(`MarketplaceStorage` 全貌)确定 `BuiltinSkillStorage` 可对齐的方法签名与 collection 获取方式。
- 读 `skill_uploads` 模块(`_parse_zip_skills` / `_parse_zip_skill_preview` 入参/出参),确认 zip 解析可复用。
- 读 `_copy_marketplace_files_to_user_skill`,确认批量复制模式。
- 读 `get_effective_skills` 完整流程,定位 builtin 合并插入点与配额计算。

## 有序 Checklist

1. **[模型/存储]** 新增 `src/infra/skill/builtin.py` `BuiltinSkillStorage`:CRUD + `list_builtin_skill_names_for_roles` + 文件批量读写;collection:`builtin_skills` / `builtin_skill_files`。
   → verify: `tests/infra/skill/test_builtin_storage.py` 通过。
2. **[zip 复用]** 实现 zip 预览/创建 → builtin(复用 `skill_uploads`,目标 builtin)。
   → verify: 单测 zip 写入 builtin + `allowed_roles`。
3. **[商城加载]** 实现 `from-marketplace`(复用批量复制,目标 builtin)。
   → verify: 单测从 marketplace 复制到 builtin。
4. **[注入]** `storage.py` `get_effective_skills`:末尾合并 builtin(角色过滤、`disabled_skills` 过滤、配额 user 优先)。
   → verify: 扩展 `tests/infra/skill/test_storage_effective_skills.py`:匹配注入、非匹配不注入、配额上限。
5. **[缓存失效]** builtin 写操作 → MVP 粗粒度失效 skills 缓存。
   → verify: 单测改动后缓存被失效。
6. **[可见性]** 确认用户侧 list/marketplace 不查 builtin(天然隔离);effective 返回不暴露 builtin 标记。
   → verify: 路由测试断言用户列表无 builtin。
7. **[API 路由]** 新增 `src/api/routes/builtin_skill.py`(表格内 6 端点),挂 `require_permissions("manage_builtin_skills")`;在 `main.py` 注册 router。
   → verify: `tests/api/test_builtin_skill_routes.py`(CRUD + 权限 403 + 可见性)。
8. **[权限]** `src/infra/auth/rbac.py` `get_default_roles`:admin 追加 `manage_builtin_skills`。
   → verify: default roles 含 key。
9. **[前端]** 新增 admin"内置技能"页(列表 + 新建:zip/商城切换 + 角色多选);`services/api/skill.ts` 加 builtin API。
   → verify: 组件测试 + 手动验证普通用户看不到该页。
10. **[测试/回归]** 全套 builtin 测试 + 现有 skill/marketplace 回归。
    → verify: `uv run pytest tests/infra/skill/ tests/api/test_builtin_skill_routes.py tests/api/test_marketplace_routes.py -q`

## Review Gates(暂停等 review)

- 步骤 4 完成后:确认注入与配额策略(user 优先)合理、缓存失效正确。
- 步骤 9 完成后:确认普通用户视角完全不可见。

## Rollback Points

- 步骤 1–3:新 collection/存储,可保留无害。
- 步骤 4–7:注入与路由为行为变更,回退代码即可;collection 数据可保留。
- 步骤 8–9:权限与 UI,可独立回退。
