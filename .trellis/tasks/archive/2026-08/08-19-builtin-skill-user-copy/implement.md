# Implementation Plan

1. 扩展 `InstalledFrom.BUILTIN`，并在 `SkillMeta` / 前端 `SkillSource` 对齐。
2. 在 skill 领域层新增：
   - 二进制 clone 到用户 key（禁止共享 `_binary_ref`）
   - `overwrite_copy_builtin_to_user(name, user_id)`
   - `ensure_role_builtin_skills_copied(user_id)`
   - `delete_skill_name_from_all_users(name)`
3. 在 `get_effective_skills` 与 `GET /api/skills/` 入口调用 ensure-copy；然后删除 builtin 运行时合并和 `include_builtin` 投影。
4. Admin `DELETE /api/admin/builtin-skills/{name}` 先清全站用户同名技能（含 S3/偏好/缓存），再删中央文档和 ZIP `_builtin` 对象。
5. 调整用户创建/商城安装/GitHub 与「角色可见 builtin 名」的 403：改为与首次注入相同的覆盖，或在 ensure-copy 之后按普通已存在技能处理。
6. 前端：技能页不再依赖只读 Builtin 投影；Admin 删除确认文案写明按名清全站。可选展示 `installed_from=builtin`，不挡编辑。
7. 重写 `.trellis/spec/backend/builtin-skills.md` 为物化合同；改相关测试。
8. 跑聚焦测试与 lint，再全量相关检查。

## Ordered checklist

- [ ] `InstalledFrom.BUILTIN` + meta 写入/读取
- [ ] clone binary to user storage key
- [ ] overwrite copy one user (delete-then-write + meta + disable-key migrate + cache)
- [ ] ensure-copy using current role eligibility; skip `installed_from=builtin`
- [ ] hook: chat/`get_effective_skills` + skills list API
- [ ] remove effective-merge and list projection
- [ ] global delete-by-name + Admin DELETE
- [ ] frontend list/actions + Admin delete copy
- [ ] spec rewrite
- [ ] tests listed below

## Validation Commands

```powershell
uv run pytest tests/api/test_builtin_skill_routes.py tests/api/test_skill_routes.py tests/infra/skill/test_builtin_storage.py tests/infra/skill/test_storage_effective_skills.py tests/infra/skill/test_storage_list_user_skills.py tests/api/test_marketplace_routes.py
uv run ruff check src
pnpm --dir frontend test -- --run
pnpm --dir frontend lint
pnpm --dir frontend build
git diff --check
```

若 GitHub 安装测试也断言 builtin 403，一并纳入。

## Risky files / rollback

| 区域 | 风险 |
|---|---|
| `src/infra/skill/storage.py` `get_effective_skills` | 去掉合并后 prompt 只剩用户目录；ensure-copy 失败会丢内置技能 |
| `src/infra/skill/builtin.py` DELETE | 按名清全站不可逆 |
| 二进制 ref | 共享 key 会在用户删除时毁掉中央/商城文件 |
| `src/api/routes/skill.py` | 投影删除后旧前端 `include_builtin` 必须无害 |
| 配额 100 | 新复制可能把第 101 个技能挤出 ls/prompt |

回滚：还原 ensure-copy 与删除扇出；已写入的用户文件保留。不要在回滚时自动清用户目录。

## Follow-up before `task.py start`

- 规划摘要已给用户，且用户明确批准最新摘要。
- `implement.jsonl` / `check.jsonl` 已有真实 spec/research 条目。
- 不在本文件完成后立刻 `start`；等批准。
