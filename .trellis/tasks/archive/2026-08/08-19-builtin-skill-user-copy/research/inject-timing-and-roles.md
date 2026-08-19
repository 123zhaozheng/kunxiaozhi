# Research: inject timing and roles

- **Query**: Natural copy hook points, user-by-role query, `allowed_roles` / `is_active` / `skill:admin`, role-change behavior, quota after copy, disabled preference keys.
- **Scope**: internal
- **Date**: 2026-08-19

## Findings

### Files Found

| File Path | Description |
|---|---|
| `src/api/routes/builtin_skill.py` | Admin create/update/delete — only current write hooks |
| `src/infra/skill/storage.py` | `get_effective_skills`, `_resolve_user_access`, quota |
| `src/infra/skill/builtin.py` | `list_builtin_skill_names_for_roles`, `is_active` filter |
| `src/infra/skill/manager.py` | Agent/session skill load facade |
| `src/infra/skill/loader.py` | Search-agent load at context setup |
| `src/agents/fast_agent/context.py` | Fast-agent load at `setup()` |
| `src/agents/search_agent/context.py` | Search-agent load at `setup()` |
| `src/api/routes/skill.py` | List projection; toggle preference keys |
| `src/infra/user/storage.py` | `list_users` — **no role filter**; `USER_LIST_LIMIT_MAX = 100` |
| `src/infra/analytics/storage.py` | Filters known user ids by `roles: role_id` |
| `src/infra/auth/` | Login/session — **no skill copy** |

### When copy could naturally run

There is **no** copy today. These are existing call sites that already touch builtin or effective skills:

| Hook | When | What happens now | Copy fit |
|---|---|---|---|
| Admin ZIP / from-marketplace success | Admin create | Write central store + bump `builtin_skills:version` | Eager fan-out to matching users |
| Admin PATCH | Roles / `is_active` / description | Metadata only; bump version | Role add/remove or deactivate — **product** |
| Admin DELETE | Admin delete | Central Mongo only + version bump | Required global name purge |
| User login / token issue | `src/infra/auth/*` | No skill I/O | Possible but **no current hook** |
| Session / first chat | `FastAgentContext.setup`, `SearchAgentContext.setup` | `get_effective_skills` / `load_skill_files` | Lazy per-user overwrite on first agent use |
| `get_effective_skills` | Prompt + Fast skills list | Runtime merge from `skill_builtin_files` | Could copy-then-read user storage; runs on cache miss (30 min) or version mismatch |
| `GET /api/skills/?include_builtin=true` | Skills page open | Projection merge, no persist | Lazy copy when user opens Skills |
| Builtin detail/file GET | Open read-only viewer | Read central files | Too late for VFS/chat unless copy already done |

Auth/login (`oa_login.py`, session Redis idle keys) never calls skill storage.

Agent setup is the first time a chatting user hits `get_effective_skills`. Users who never chat and never open Skills never hit it.

`list_user_skills` with default `include_builtin=false` does not load builtins. The UI always passes `includeBuiltin: true`.

### How many users might be copied to? Is there a user-by-role query?

**No dedicated `list_users_by_role`.**

`UserStorage.list_users` (`user/storage.py` 445–483): filters `is_active` and username/email search only. Hard cap `USER_LIST_LIMIT_MAX = 100` (`storage.py` 20, 55–56). Pagination via skip/limit. **No `roles` index** (`_ensure_indexes` creates username, email, oauth_provider, tokens — not `roles`).

`UserStorage.count_users` also has no role filter.

Analytics (`analytics/storage.py` 1017–1022) does `users.find({"_id": {"$in": object_ids}, "roles": role_id})` — array containment on **already known** ids, not a full role scan.

Usable Mongo shapes (not wrapped today):

```js
// role-scoped builtin
{ roles: { $in: allowed_roles } }

// unscoped builtin (allowed_roles empty / missing) = all users
{}

// optional: only active accounts
{ is_active: { $ne: false }, roles: { $in: allowed_roles } }
```

`skill:admin` bypass: `_resolve_user_access` sets `is_admin` if any of the user’s roles has permission `skill:admin` (`storage.py` 977–981). Those users see **all active** builtins regardless of `allowed_roles` (`builtin.py` 239–246). Matching them for eager copy requires:

1. `roles` collection: names whose `permissions` contain `skill:admin`
2. `users` with `roles: { $in: those_role_names }`

That join is not a single existing API.

Empty `allowed_roles` means **all roles** for injection (`builtin.py` 235–236). Eager copy of an unscoped builtin is a full user scan.

User count is not in-repo; `count_users()` can measure it at design/implement time. Admin list cap 100 is **not** a total-user cap.

### `allowed_roles` / `is_active` / `skill:admin` current semantics

| Input | Injection (`list_builtin_skill_names_for_roles`) | User list projection | User writes |
|---|---|---|---|
| `is_active=false` | excluded (`is_active: {$ne: False}`) | excluded | 403 if still “visible”? inactive names are not returned by list-for-roles, so `get_builtin_skill_for_user` misses them → 404 not 403 if no personal files |
| `allowed_roles=[]` or missing | all non-admin-filtered users | same | 403 for builtin-only |
| intersection with user.roles | included | same | 403 |
| no intersection | excluded | excluded | 404 (not found), user may create a personal skill of that name |
| `skill:admin` | **all active** builtins | same | 403 for those names |

`is_active` PATCH does **not** delete central files or user files (there are no user copies). Prompt/list drop the skill after cache invalidation via version bump.

Admin (`is_admin`) is permission `skill:admin`, **not** role name `admin`. A user with role `admin` only gets the bypass if that role’s permissions include `skill:admin`.

### What happens today if role changes

`UserStorage` update of `roles` (`storage.py` 364–365) does **not** call `invalidate_user_cache`.

Until `user_skills:{id}` TTL (30 min) or a `builtin_skills:version` bump:

- Prompt/list may still show old role-matched builtins.
- VFS never showed them anyway.

After cache recompute, `_resolve_user_access` reads **current** roles. Lost role → builtin disappears from effective merge. Gained role → appears. **No file add/remove** in `skill_files`.

There is no listener on role assignment (`src/api/routes/user.py` role updates, admin user PATCH).

### Quota: `SKILL_EFFECTIVE_LOAD_LIMIT` after copy

Constant: 100 (`storage_helpers.py` 8).

Today:

- Personal enabled names consume quota first (`storage.py` 881–883, 925).
- Disabled **personal** names still shadow builtins but are not loaded into `result["skills"]`.
- Remaining quota filled with role-eligible builtins (`storage.py` 914–926).
- VFS `ls`/`grep`/`glob` also cap `get_all_user_skill_names` at 100 (`skills_store.py` 436–438).

After copy into `skill_files`:

- Copied names **are** personal names. They count toward the 100 **before** any remaining builtin merge.
- If projection merge is removed, the 100 cap is entirely personal (including copies).
- A user with 100 personal skills who receives a copy: `get_all_user_skill_names` sorts by name and `$limit 100` (`storage.py` 1128–1135) — the copied skill may be **dropped from ls/prompt** if sorted after the 100th name.
- Same-name overwrite does **not** increase count (replaces existing name).
- New copy to a user under 100 increases count by 1 per skill.

`BUILTIN_SKILL_NAMES_LIMIT = 100` (`builtin.py` 40) only caps how many builtins are considered for merge, not user storage.

Frontend list with `include_builtin` currently uses personal_enabled_count vs 100 to size builtin remaining quota (`skill.py` 343). After copy + dropping projection, list is ordinary `list_user_skills` pagination (page size 20).

### `disabled_builtin_skill_names` vs `disabled_skills` after copy

Today (`skill.py` 858–879, spec):

- Personal name (files exist) → toggle writes `disabled_skills`.
- Builtin-only (no personal files) → toggle writes `disabled_builtin_skill_names`.
- Effective merge uses **only** `disabled_builtin_skill_names` for builtins, **only** `disabled_skills` for personal (`storage.py` 875–883 vs 918–923).
- A disabled personal name still **shadows** a same-name builtin (builtin never injected).

After copy, `list_skill_file_paths` is non-empty → toggle will write `disabled_skills`. `disabled_builtin_skill_names` becomes dead for that name unless migrated.

If a user had disabled the projected builtin, then copy lands as a new personal skill **enabled** unless copy also copies the disable flag into `disabled_skills`. **Product vs engineering:** whether disable survives copy.

Global delete-by-name should strip the name from **both** lists (user DELETE today only strips `disabled_skills`).

## Related Specs

- `.trellis/spec/backend/builtin-skills.md` — quota, independent disable keys, `skill:admin` not named there; code uses `skill:admin`.
- Archive inject design: user-first quota, coarse cache version.

## Caveats / Not Found

- No login-time skill hook.
- No role-change hook / cache invalidation on role PATCH.
- No `list_users_by_role`; analytics filter is not a full scan.
- `skill:admin` user set requires a roles→users join not packaged as a helper.
- Exact production user cardinality unknown (call `count_users` at implement time).
