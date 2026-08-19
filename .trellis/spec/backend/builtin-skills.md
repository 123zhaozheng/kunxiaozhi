# Builtin Skill Materialization

## Contract

Admin Builtin Skills live in `skill_builtin` and `skill_builtin_files`.
Matching users receive a **lazy overwrite-copy** into their personal
`skill_files` (virtual `/skills/{name}/`). After copy, list, prompt, VFS
`ls`/`read`/`transfer`, edit, disable, delete, and publish all use the
existing user-skill paths. There is no runtime merge and no list
projection.

Copy is **once per user per name**: skip when that user's `__meta__`
has `installed_from=builtin`; otherwise overwrite (manual, marketplace,
or missing). Do **not** skip merely because files exist — that would
leave a pre-existing personal same-name skill unoverwritten.

Copied binaries must be cloned to `skills/{user_id}/...`; never copy a
shared `_binary_ref` storage key. Bulk user-skill deletes
(`delete_skill_files`, `delete_skill_and_meta`) must only remove S3
objects whose key starts with `skills/{user_id}/`. Foreign marketplace
or `skills/_builtin/` keys stay.

```text
chat setup / GET /api/skills/
  -> ensure_role_builtin_skills_copied(user_id)
  -> for each role-eligible active builtin name:
       skip if installed_from == builtin
       else delete-then-write central files into skill_files
  -> read skill_files only
```

Eligibility matches `list_builtin_skill_names_for_roles`: `is_active`
only; empty `allowed_roles` means all users; `skill:admin` copies every
active builtin. Role loss, deactivation, and `allowed_roles` shrink do
**not** reclaim copies. Inactive builtins are not copied to new users.

Admin delete removes the central skill **and** that `skill_name` from
every user space (including coincidental personal skills that were never
injected), with per-user S3 cleanup and `invalidate_user_cache`. ZIP
objects under `skills/_builtin/{name}/` are deleted; marketplace-shared
keys must not be deleted.

## Validation

- Admin ZIP / marketplace create writes only the central store.
- Ensure-copy is best-effort: log per-skill failures and do not block
  chat.
- After copy, disable uses `disabled_skills`. Migrate
  `disabled_builtin_skill_names` into `disabled_skills` on copy.
- User self-delete has no tombstone; the next visit recopies if still
  role-matched.
- `GET /api/skills/?include_builtin=` remains a compatibility no-op.

## Scenario: lazy copy and global delete-by-name

### 1. Scope / Trigger

Use this contract whenever a matching user opens chat (`get_effective_skills`)
or the Skills page (`GET /api/skills/`), and whenever an admin deletes a
Builtin. Do not fan out on Admin create.

### 2. Signatures

Implementation lives in `src/infra/skill/builtin_copy.py`.
`SkillStorage.ensure_role_builtin_skills_copied` and
`iter_user_ids_for_skill_name` are thin facades.

- `ensure_role_builtin_skills_copied(user_id, *, storage=None, builtin_storage=None) -> None`
  copies eligible active builtins that the user does not already hold as
  `installed_from=builtin`. Best-effort: log and continue on per-skill
  failure.
- `overwrite_copy_builtin_to_user(skill_name, user_id, *, storage=None, builtin_storage=None) -> None`
  `delete_skill_files`, clone batches, upsert, then
  `set_skill_meta(..., installed_from=builtin)`, migrate disable prefs,
  `invalidate_user_cache`.
- `delete_skill_name_from_all_users(skill_name, *, storage=None) -> int`
  returns the number of users cleaned. Each user:
  `delete_skill_and_meta` + strip disabled/pinned/favorite +
  `invalidate_user_cache`.
- `delete_builtin_namespace_objects(skill_name) -> None` deletes ZIP
  objects under `skills/_builtin/{name}/` only.
- `GET /api/admin/builtin-skills/marketplace?skip=<int>&limit=<int>`
  still lists Marketplace sources for Builtin admins
  (`manage_builtin_skills`) without requiring `marketplace:read`.
- `GET /api/skills/?include_builtin=` lists persisted personal Skills
  only (after ensure-copy). The query is a compatibility no-op.
- `GET /api/skills/{name}` does **not** run ensure-copy. Callers must
  hit chat setup or the list endpoint first (the Skills UI lists then
  opens detail).
- `DELETE /api/admin/builtin-skills/{name}` calls
  `delete_skill_name_from_all_users`, then deletes central rows, then
  `delete_builtin_namespace_objects`, then bumps
  `builtin_skills:version`. Version bump alone does not drop copied
  names from `user_skills:{id}` caches.

### 3. Contracts

- Copied entries are normal user Skills. `installed_from` may be
  `"builtin"` for display; write actions stay enabled.
- Quota remains `SKILL_EFFECTIVE_LOAD_LIMIT`. Copied names count as
  personal names. Same-name overwrite does not increase count.
- SkillsStoreBackend keeps `/skills/{name}/` → user documents. After
  copy those paths exist.
- User-side ZIP / GitHub / Marketplace install no longer 403 on a
  role-visible Builtin name. If the user already has files, existing
  duplicate rules apply; otherwise the install may land and the next
  ensure-copy overwrites unless `installed_from` is already `builtin`.

### 4. Validation & Error Matrix

| Condition | Expected behavior |
| --- | --- |
| Admin has `manage_builtin_skills` but not `marketplace:read` | Admin Marketplace source list succeeds |
| Ensure-copy fails for one skill | Log a warning; continue remaining skills; chat still loads user Skills |
| First visit; user has a same-name manual/marketplace skill | Overwrite with Admin content and set `installed_from=builtin` |
| User already has `installed_from=builtin` | Skip; user edits survive |
| Admin delete of `{name}` | Remove that name from all user spaces, including never-injected personal skills |
| Admin delete of a ZIP builtin | Delete `skills/_builtin/{name}/` objects; do not delete marketplace keys |
| User deletes a copied skill | Next chat or Skills page recopies if still eligible |
| Bulk delete of a user skill that still holds a marketplace `_binary_ref` | Mongo rows go away; the shared S3 object is **not** deleted |
| `is_active=false` / role loss / smaller `allowed_roles` | Existing copies stay; new matching users are not copied that name |

### 5. Good / Base / Bad Cases

- Good: an analyst chats once and `/skills/{name}/SKILL.md` exists for
  every eligible builtin; later edits persist on the next visit.
- Base: a user has no eligible builtins; list and prompt behave like
  the personal-only catalog.
- Bad: prompt advertises `/skills/{name}` while VFS has no user files,
  or a copied `_binary_ref` still points at `_builtin` / marketplace
  keys so a user delete destroys shared objects.

### 6. Tests Required

- `tests/infra/skill/test_builtin_copy.py`: first-visit overwrite, skip
  when `installed_from=builtin`, clone binaries to a user key, global
  delete-by-name including another user's personal same-name skill.
- `tests/infra/skill/test_storage_effective_skills.py`:
  `get_effective_skills` has no dual builtin merge after copy.
- `tests/api/test_skill_routes.py`: list ensure-copy, `include_builtin`
  is ignored, copied skills are writable.
- `tests/api/test_builtin_skill_routes.py`: Admin DELETE fans out by
  name then invalidates cache.
- GitHub and Marketplace route tests: install is not rejected solely
  because a role-visible Builtin exists.
- Frontend: SkillCard write actions are not gated on `is_builtin`.

### 7. Wrong vs Correct

```python
# Wrong: merge central files into the prompt without writing skill_files.
result["skills"].update(await storage._get_builtin_skills_for_user(...))

# Correct: copy into the user document, then read skill_files only.
await storage.ensure_role_builtin_skills_copied(user_id)
result = await storage.get_effective_skills(user_id)  # user files only

# Wrong: skip copy whenever the user already has files of that name.
if await storage.list_skill_file_paths(name, user_id):
    continue

# Correct: skip only a prior builtin copy; overwrite manual/marketplace.
meta = await storage.get_skill_meta(name, user_id)
if meta is not None and meta.installed_from == InstalledFrom.BUILTIN:
    continue

# Wrong: delete every `_binary_ref` S3 object while wiping a user skill.
await storage_service.delete_file(binary_ref.storage_key)

# Correct: only delete objects under this user's prefix.
if storage_key.startswith(f"skills/{user_id}/"):
    await storage_service.delete_file(storage_key)
```
