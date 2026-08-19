# Research: current injection and VFS

- **Query**: Map current builtin create/storage, `get_effective_skills` merge, SkillsStoreBackend VFS, frontend `include_builtin`, prompt/loader injection, and the exact prompt-vs-VFS gap.
- **Scope**: internal
- **Date**: 2026-08-19

## Findings

### Files Found

| File Path | Description |
|---|---|
| `src/api/routes/builtin_skill.py` | Admin create/upload/update/delete routes |
| `src/api/main.py` | Router mount at `/api/admin/builtin-skills` |
| `src/infra/skill/builtin.py` | Central `skill_builtin` / `skill_builtin_files` storage |
| `src/infra/skill/constants.py` | Collection names + Redis version key |
| `src/infra/skill/storage.py` | User `skill_files` + `get_effective_skills` merge |
| `src/infra/skill/manager.py` | Facade used by agents |
| `src/infra/skill/loader.py` | Prompt path `/skills/{name}/SKILL.md` |
| `src/infra/skill/middleware.py` | Alternate prompt injection from effective skills |
| `src/infra/backend/skills_store.py` | Agent VFS: ls/read/grep/glob/transfer |
| `src/api/routes/skill.py` | User list/detail/file + `include_builtin` projection |
| `frontend/src/components/panels/SkillsPanel/useSkillsActions.ts` | Always lists with `includeBuiltin: true` |
| `src/agents/fast_agent/context.py` | Loads skills via `SkillManager.get_effective_skills` |
| `src/agents/search_agent/context.py` | Loads skills via `load_skill_files` |

### Admin create / upload routes

Mounted in `src/api/main.py` (~703–708) at prefix `/api/admin/builtin-skills`. All endpoints require `manage_builtin_skills`.

| Method | Path | Function | Writes user `skill_files`? |
|---|---|---|---|
| GET | `/` | `list_builtin_skills` | no |
| GET | `/marketplace` | `list_marketplace_sources_for_builtin` | no |
| POST | `/zip/preview` | `preview_zip_skills` | no |
| POST | `/zip` | `upload_builtin_skill_from_zip` | **no** — only `BuiltinSkillStorage.import_parsed_skills` |
| POST | `/from-marketplace` | `create_builtin_from_marketplace` | **no** — only `create_from_marketplace` |
| PATCH | `/{name}` | `update_builtin_skill` | no (metadata only: roles/description/`is_active`) |
| DELETE | `/{name}` | `delete_builtin_skill` | **no** — central collections only |

ZIP create (`builtin_skill.py` 143–184): parse via `_parse_zip_skills`, then `import_parsed_skills(parsed, allowed_roles, user.sub)`. Duplicate names raise 409.

Marketplace create (`builtin_skill.py` 192–208): `create_from_marketplace(marketplace_name, allowed_roles, created_by)`. File batches copied from marketplace into **builtin** files, not user files (`builtin.py` 505–544). Binary refs are copied as JSON pointing at the marketplace storage key (comment at `builtin.py` 514–515).

ZIP binary files are uploaded under namespace `_builtin`: `set_builtin_binary_file` uses `build_storage_key(BUILTIN_BINARY_NAMESPACE, skill_name, file_path)` → `skills/_builtin/{name}/{path}` (`builtin.py` 36, 324–352; `binary.py` 182–184).

Admin create does **not** fan out to users. After write it only bumps Redis `builtin_skills:version`.

### Central storage collections

From `src/infra/skill/constants.py` 6–10:

- User files: `skill_files` (docs keyed by `skill_name` + `user_id` + `file_path`, including `__meta__`)
- Builtin metadata: `skill_builtin` (unique `skill_name`)
- Builtin files: `skill_builtin_files` (unique `(skill_name, file_path)`)

Indexes (`builtin.py` 74–85): unique `skill_name` on meta; unique `(skill_name, file_path)` on files. **No `allowed_roles` index.**

User indexes (`storage.py` 55–62): unique `(skill_name, user_id, file_path)` only. Leftmost prefix can serve `skill_name`-only queries.

### `get_effective_skills` merge

`SkillStorage.get_effective_skills` (`storage.py` 818–960):

1. Redis cache `user_skills:{user_id}` (TTL 1800s). Payload carries `_builtin_version`. Mismatch vs `builtin_skills:version` forces recompute (`storage.py` 859–870, 945–956).
2. Load **all** personal names (`get_all_user_skill_names`) — disabled names still occupy the name for shadowing (`storage.py` 878–883).
3. Load enabled personal files, cap `SKILL_EFFECTIVE_LOAD_LIMIT` (100).
4. Resolve roles via `UserStorage.get_by_id` + `RoleStorage.get_by_names`. `is_admin` is `"skill:admin" in permissions` (`storage.py` 962–981).
5. Load `disabled_builtin_skill_names` (independent of `disabled_skills`) (`storage.py` 918, 1097–1110).
6. `_get_builtin_skills_for_user` (`storage.py` 986–1042):
   - `list_builtin_skill_names_for_roles(user_roles, is_admin)`
   - drop names in `shadowed_names` (any personal name, including disabled)
   - drop `disabled_builtin_skill_names` unless `include_disabled`
   - slice remaining quota
   - `batch_get_builtin_skill_files`
   - merge dicts with `is_builtin: True` (comment at 849 claims “no builtin marker”; implementation **does** set it at 1040)

`list_builtin_skill_names_for_roles` (`builtin.py` 227–254):

- `is_active != False`
- `is_admin=True`: **all** active builtins, no role clause (`test_list_builtin_skill_names_admin_skips_role_clause`)
- else: `$or` of `allowed_roles $in user_roles`, empty array, or missing field
- cap `BUILTIN_SKILL_NAMES_LIMIT = 100`

Builtin merge failure is swallowed: log warning, return personal skills only (`storage.py` 940–943).

`SkillManager.get_effective_skills` (`manager.py` 77–88) unwraps `result["skills"]` and passes only `disabled_skills` (not builtin disabled names). Storage re-reads builtin disabled names internally.

### SkillsStoreBackend: does it see builtins?

**No.** Every VFS path reads `SkillStorage` user documents only.

| Op | Source | Builtin? |
|---|---|---|
| `als("/")` | `get_all_user_skill_names(user_id)` (`skills_store.py` 435–456) | no |
| `als("/{name}/")` | `list_skill_file_paths(name, user_id)` (203–208, 469–487) | no |
| `aread` | `get_skill_file(name, file, user_id)` (242–251) | no; missing skill → `"Skill '{name}' not found"` |
| `agrep` root | same name list + `batch_get_skill_files` (691–714) | no |
| `aglob` root | same name list (789–801) | no |
| `adownload_files` (transfer) | `batch_get_skill_files([(name, user_id)])` (525–557) | no; missing → `file_not_found` |

`transfer_file` / `transfer_path` (`src/infra/tool/transfer_file_tool.py` 6–7) route `/skills/*` to this backend. There is no builtin overlay on SkillsStoreBackend (Persona overlay was designed then abandoned; current spec forbids it).

Root listing is also capped at `SKILL_EFFECTIVE_LOAD_LIMIT` (100). Copied builtins would compete with personal names in that cap.

### Frontend `include_builtin`

`GET /api/skills/?include_builtin=` (`skill.py` 280–457):

- Default `false`: personal `list_user_skills` only.
- `true`: load personal page unbounded up to 100, then `list_builtin_skills_for_user` with `shadowed_names=personal_names`, remaining quota `100 - personal_enabled_count`. Merge personal **then** builtin. Re-slice skip/limit after merge. Projected rows set `is_builtin=true`, `installed_from="builtin"`.

User Skills UI always requests the projection:

```36:42:frontend/src/components/panels/SkillsPanel/useSkillsActions.ts
  const listParams = useMemo(
    () => ({
      skip: (page - 1) * pageSize,
      limit: pageSize,
      q: searchQuery.trim() || undefined,
      includeBuiltin: true,
```

Read-only UI for `is_builtin`:

- `SkillsPanel/index.tsx` 107: editor `readOnly={editingSkill?.is_builtin}`
- `SkillCard.tsx` 76–81, 201–214, 228, 274: no select/pin/favorite/publish/delete; Eye instead of Edit
- Toggle still shown; backend `PATCH /{name}/toggle` writes `disabled_builtin_skill_names` when no personal files (`skill.py` 858–879)

Detail/file reads (`skill.py` 460–563): if personal `list_skill_file_paths` is empty, fall back to `get_builtin_skill_for_user`. If personal name exists but requested file is missing → 404, **no** builtin fallback (tested).

Writes (`_ensure_user_skill_writable`, `skill.py` 80–99): personal files win; builtin-only name → 403 `"Builtin skill is read-only"`. Same gate on ZIP upload, marketplace install, GitHub install, publish.

### Prompt / loader injection

Search agent: `load_skill_files` (`loader.py` 23–97) → `SkillManager.get_effective_skills()` → for each enabled skill, files keyed as `/{skill_name}/{file_name}` (no `/skills` prefix in the in-memory file map) and prompt text:

```109:119:src/infra/skill/loader.py
    for skill in skills:
        name = skill.get("name", "未命名技能")
        description = skill.get("description", "无描述")
        skill_path = f"/skills/{name}/SKILL.md"
        desc_line = f"- **{name}**: {description}"
        skills_lines.append(desc_line)
        skills_lines.append(f"  -> 读 `{skill_path}` 获取完整说明")
```

Prompt also tells the model to `ls("/skills/")`, `read_file`, `transfer_file`/`transfer_path`.

Fast agent: `FastAgentContext.setup` (`fast_agent/context.py` 216–228) appends the same effective-skill dicts (including builtins) to `self.skills`.

`SkillsMiddleware.inject_skills_async` (`middleware.py` 41–84) also lists effective skills with ``/skills/{name}/SKILL.md``.

None of these paths copy files into `skill_files`. They only merge builtin **content** into the prompt/list.

### Exact gap: prompt says `/skills/{name}` but VFS misses builtins

Confirmed current behavior:

1. Role-matched builtin is merged into `get_effective_skills` from `skill_builtin_files`.
2. Prompt/loader therefore advertise `/skills/{name}/SKILL.md`.
3. `SkillsStoreBackend.ls("/skills/")` lists only `skill_files` for that `user_id`.
4. `read("/skills/{name}/SKILL.md")` and transfer download call `get_skill_file` / `batch_get_skill_files` on user storage → not found.

Same-name personal skill **shadows** the builtin in both prompt and list (disabled personal name still shadows). VFS then shows the personal copy, which is consistent for that name only.

Frontend list can show the builtin (projection). Agent VFS cannot. That is the user-visible “提示词里有、目录里没有” bug this task is reversing.

## Related Specs

- `.trellis/spec/backend/builtin-skills.md` — current contract is **projection, never materialize**. This task intentionally reverses that.
- `.trellis/spec/backend/persona-marketplace-skills.md` — Persona also does **not** write `skill_files` today (hints only).
- Archive: `07-31-builtin-skill-inject` (runtime merge, user-invisible), `07-31-builtin-skill-visibility` (list projection, explicit “do not copy”).

## Caveats / Not Found

- No SkillsStoreBackend builtin overlay exists in current code (Persona overlay is not present).
- `get_effective_skills` docstring says merged results “不暴露 builtin 标记”, but `_get_builtin_skills_for_user` sets `is_builtin: True`. Prompt builders ignore the flag; VFS never sees the merged dict anyway.
- Team agent does not call `get_effective_skills` / `load_skill_files` directly (no matches under `src/agents/team_agent`).
