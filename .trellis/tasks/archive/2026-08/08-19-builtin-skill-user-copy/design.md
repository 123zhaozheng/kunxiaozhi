# Technical Design

## Boundaries

- `BuiltinSkillStorage` 仍是 Admin 中央源（`skill_builtin` / `skill_builtin_files`）。
- 新领域函数负责「惰性复制到一个用户」和「按名删除所有用户副本」。不要把扇出写进 HTTP 路由细节里。
- `SkillStorage.get_effective_skills` 与 `GET /api/skills/` 在读用户技能**之前**调用 ensure-copy；之后只读 `skill_files`。
- `SkillsStoreBackend` 不改路径合同（仍是 `/skills/{name}/` → 用户文档）。复制完成后它自然能 `ls`/`read`/`transfer`。
- 前端去掉 Builtin 只读投影依赖；复制后的条目走普通 SkillCard。

## Data Flow

### Lazy copy (per matching user)

```text
chat setup / GET /api/skills/
  -> ensure_role_builtin_skills_copied(user_id)
  -> list active builtins for this user's roles (same eligibility as today, including skill:admin)
  -> for each name:
       if user __meta__.installed_from == builtin: skip
       else: overwrite-copy from central files
  -> then existing list/get_effective_skills reads only skill_files
```

Skip 条件必须看 `installed_from=builtin`，不能只看「同名是否存在」。否则用户原有手写/商城同名技能永远不会被首次覆盖。

### Overwrite copy (one user, one name)

1. `delete_skill_files(name, user_id)` 清掉旧文件和该用户自己的 S3 对象。
2. 按 batch 读取 `iter_builtin_file_batches`。
3. 文本写入用户文档；二进制 **clone bytes** 到 `skills/{user_id}/{name}/...`，再写新的 `_binary_ref`。禁止原样拷贝中央/商城 key。
4. `set_skill_meta(..., installed_from=builtin)`。
5. 若 `disabled_builtin_skill_names` 含该名，迁到 `disabled_skills` 后去掉旧键。
6. `invalidate_user_cache(user_id)`。

用 delete-then-write / `sync_skill_files`，不要只用 `upsert_skill_files_batch`（会留下旧路径）。

### Admin delete (global by name)

```text
DELETE /api/admin/builtin-skills/{name}
  -> list distinct user_id from skill_files where skill_name == name (batched)
  -> per user: delete_skill_and_meta + strip disabled/pinned/favorite
               + disabled_builtin_skill_names + invalidate_user_cache
  -> delete central meta/files
  -> delete ZIP builtin S3 keys under skills/_builtin/{name}/
  -> do NOT delete marketplace-shared S3 keys
  -> bump builtin_skills:version (harmless leftover)
```

按名删除是产品规则：未注入过的同名个人技能一并删除。

### Prompt / VFS after change

- `get_effective_skills` 不再 merge builtin collections。
- Loader / middleware / Fast / Search 继续只消费 effective user skills。
- VFS 仍只读用户文档；因为文件已在用户目录，路径 `/skills/{name}/SKILL.md` 成立。

## Contracts

- `InstalledFrom` 增加 `builtin`。
- `ensure_role_builtin_skills_copied(user_id) -> None`：best-effort 不应阻断聊天；单技能失败记日志并继续其余。
- `delete_skill_name_from_all_users(skill_name) -> int`：返回清理的用户数。
- `GET /api/skills/`：`include_builtin` 可保留为无操作兼容参数，不再投影中央文件。
- Builtin-only 写 403、商城安装因「角色可见 builtin」而 403 的路径：复制后该名已是个人技能，走已存在/覆盖的普通规则。首次复制发生在用户访问技能页或聊天时，因此从未登录的匹配用户在 Admin 刚创建后、自己进来前，商城安装同名仍可能撞上中央 builtin 检查——实现时应改为：中央存在同名 builtin 时，安装路径允许稍后被惰性覆盖，或与 ensure-copy 使用同一覆盖语义。推荐：用户侧创建/安装同名在角色匹配时直接走覆盖复制（与首次注入一致），避免 403 和惰性窗口打架。

## Eligibility

与今天 `list_builtin_skill_names_for_roles` 一致：

- `is_active` 才写入新用户。
- `allowed_roles` 空 = 所有用户。
- `skill:admin` 用户对全部 active builtin 执行 ensure-copy。

停用、丢角色、改角色：**不**删除已写入文件。ensure-copy 对不再匹配的名字简单跳过（已是 builtin 副本则本来就会 skip）。

## Cache

复制或全局删除改了 `skill_files` 后，必须对每个受影响 `user_id` 调 `invalidate_user_cache`。只 bump `builtin_skills:version` 不够。

## Quota

复制后的名字计入 `SKILL_EFFECTIVE_LOAD_LIMIT`（100）。同名覆盖不增加数量。超过 100 时与今天个人技能一样可能被 `get_all_user_skill_names` 截断，本任务不扩大配额。

## Frontend

- `useSkillsActions` 可继续传 `includeBuiltin`，后端忽略投影。
- SkillCard 不再把这些条目当只读；`is_builtin` 列表投影删除后应变为 false，或仅用于展示 `installed_from=builtin` 标签（可选，非 MVP）。MVP：当普通技能，不必特殊只读。
- Admin Builtin 管理页删除文案应提示：将从所有用户空间按名删除，含碰巧同名的个人技能。

## Compatibility / Migration

- 已靠投影看到、但从未落入 `skill_files` 的用户：下次聊天或打开技能页时执行首次覆盖写入。
- 已有同名个人技能的匹配用户：该次进入会被覆盖（Admin 权威）。
- 不需要离线全站回填。

## Rollback

- 回滚代码后停止 ensure-copy；已写入用户目录的文件仍在，可当普通技能保留。
- 全局删除逻辑回滚后，Admin 删除再次只删中央库。
- 不自动把用户目录里的副本收回中央投影模型。

## Trade-offs

- 惰性 + 只写一次：实现简单，用户改动能留住；Admin 内容更新必须先删再放。
- 按名全球删除：实现简单，会删掉从未注入的同名个人技能（已确认接受）。
- 用户自行删除后，角色仍匹配则会再次写入（不做墓碑）。
- `skill:admin` 会惰性获得全部 active builtin，可能覆盖其同名个人技能。
