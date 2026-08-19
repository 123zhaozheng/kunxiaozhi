# 将内置技能复制进用户技能空间

## Goal

Admin 创建的内置技能必须真实写入匹配用户的个人技能目录（`skill_files` / `/skills/{name}/`）。写入之后，列表、`ls`、`read`、`transfer`、上下文注入全部走现有用户技能逻辑。用户和 agent 都能在 `/skills/` 找到文件，不再出现「提示词里有、目录里没有」。

## User Stories

- **Admin 创建/上传内置技能**：只写中央库。匹配角色用户下次聊天或打开技能页时，写入其个人目录；已有同名技能则覆盖。
- **匹配角色的用户**：在 `/skills/{name}/` 看到该技能，之后按自己的技能编辑、禁用、删除、发布。
- **Admin 更新内容**：先删除再放。删除按技能名清掉所有用户空间（含碰巧同名的个人技能），再放入最新版；用户再进来时写入新内容。
- **非匹配角色用户**：不会被写入。但 Admin 删除时，他们碰巧同名的个人技能也会被删掉。

## Background

当前内置技能只在 `skill_builtin` / `skill_builtin_files` 做运行时投影。Prompt 经 `get_effective_skills` 合并后告诉模型去读 `/skills/{name}/SKILL.md`，但 `SkillsStoreBackend` 只读用户 `skill_files`，所以目录是空的。前端 `include_builtin` 只是列表投影，文件仍不落用户空间。Admin 删除也不碰用户目录。

这与 Persona 技能曾经的 overlay 坑同类。本任务有意反转 `.trellis/spec/backend/builtin-skills.md` 的「不得物化、用户名优先、只读投影」合同。

调研：`.trellis/tasks/08-19-builtin-skill-user-copy/research/`。

## Requirements

1. Admin 创建内置技能（ZIP 或商城）只写中央存储，不扇出全站用户。
2. 匹配角色用户（含 `skill:admin` 可见的全部 active 内置技能）在下次聊天或打开技能页时惰性写入个人目录。
3. **只写一次**：该用户尚未持有 `installed_from=builtin` 的该技能名时，同名覆盖（手写/商城/缺失都盖）。已是 builtin 副本则跳过，保留用户改动。
4. 写入后按普通用户技能处理：可编辑、禁用、删除、发布；走 `disabled_skills`，不再用 `disabled_builtin_skill_names` 投影。
5. 丢角色、缩小 `allowed_roles`、停用（`is_active=false`）都不从用户目录收回。新匹配用户下次进来再惰性补齐。停用后新用户不再写入。
6. Admin 删除：删中央记录，并按技能名从**所有**用户空间删除（含从未注入、碰巧同名的个人技能），清理该用户自己的 S3 对象与偏好名，并失效对应用户 skills 缓存。
7. 删除后再创建：用户再进来时按首次注入覆盖写入新内容。
8. 去掉 `get_effective_skills` 的 builtin 运行时合并，以及 `GET /api/skills/?include_builtin=` 投影旁路，避免同一技能两份。
9. 复制二进制时克隆到 `skills/{user_id}/...`，禁止共享中央/商城 S3 key。
10. 与任何内置技能不同名的既有用户技能不受影响。

## Out of Scope

- 静默把 Admin PATCH 内容同步到已写入用户（更新路径是先删再放）。
- 保留运行时投影 / overlay 双轨。
- Builtin 版本号、多版本共存。
- 从某用户私有技能提升为 builtin。
- 用户自行删除后的「墓碑」：角色仍匹配时，下次进入会再次写入。
- 角色变更时主动收回已写入技能。

## Acceptance Criteria

- [ ] 匹配用户首次聊天或打开技能页后，`/skills/{name}/` 存在完整文件（含 `SKILL.md`）。
- [ ] 首次写入时，用户已有同名技能被覆盖为 Admin 内容。
- [ ] 已写入后用户修改保留，下次进入不会被中央内容盖掉。
- [ ] Agent `ls("/skills/")`、`read("/skills/{name}/SKILL.md")`、`transfer_file`/`transfer_path` 与普通用户技能一致。
- [ ] 上下文注入包含该技能，且不出现投影与用户目录双份。
- [ ] 前端技能页把它当普通技能展示（可编辑），不再依赖 `include_builtin` 投影。
- [ ] Admin 删除后，所有用户空间中的该技能名消失，含碰巧同名的个人技能。
- [ ] Admin 删除后再创建，匹配用户再进入得到新内容。
- [ ] 非匹配角色用户不会被写入；Admin 删除仍会删掉他们的同名技能。
- [ ] 丢角色 / 停用 / 缩小允许角色后，已写入用户仍保留该技能。
- [ ] 与任何内置技能不同名的既有用户技能不受影响。
- [ ] 复制的二进制删除时不会毁掉中央或商城 S3 对象。

## Key Decisions

| 决策 | 选择 |
|---|---|
| 存放位置 | 复制进用户 `skill_files`，不投影 |
| 同名 | 首次注入覆盖，之后不覆盖 |
| 触发 | 惰性：下次聊天或打开技能页 |
| 更新 | Admin 先删再放，不静默同步 |
| 删除 | 按技能名清全站，含碰巧同名个人技能 |
| 收回 | 只有 Admin 删除；丢角色/停用/改角色不收回 |
| 写入后权限 | 当普通技能，可编辑/禁用/删除/发布 |

## Notes

- Planning 完成。需用户批准本规划摘要后才可 `task.py start` 与改产品代码。
