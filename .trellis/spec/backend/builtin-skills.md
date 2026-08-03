# Builtin Skill Effective Projection

## Contract

Builtin Skills are stored once in `skill_builtin` and `skill_builtin_files` and
are projected into a user's effective Skill view only when the user's roles
match `allowed_roles` (or the Builtin is unscoped). They are read-only system
content; all user-facing write paths must resolve a same-name user Skill first
and reject Builtin-only writes.

Resolution order is exact and shared by list, prompt, single-file reads, batch
transfers, and agent Skill loading:

```text
user Skill name (including disabled names) > role-eligible Builtin > missing
```

A disabled user Skill still shadows a Builtin of the same name. Builtin
preferences use independent metadata keys such as
`disabled_builtin_skill_names`; never reuse `disabled_skills`, because user
Skill preferences must not change Builtin visibility or content.

Builtin writes invalidate the global Builtin version. Effective user caches
carry that version and recompute on mismatch without copying Builtin files into
user storage.

## Validation

- Role filtering is enforced server-side and must not rely on frontend hiding.
- Builtin-only upload, edit, rename, delete, and publish operations return a
  permission error.
- A same-name user Skill always remains the source for reads and transfers.
- If Builtin loading fails, return ordinary user Skills and degrade without
  blocking chat.

## Scenario: Admin source discovery and user-visible Builtin catalog

### 1. Scope / Trigger

Use this contract whenever the Builtin administration page selects a
Marketplace source, or a user-facing Skill list/detail/file/write endpoint may
resolve a role-visible Builtin Skill. The catalog is a projection over central
Builtin storage; it must never materialize a writable personal copy.

### 2. Signatures

- `GET /api/admin/builtin-skills/marketplace?skip=<int>&limit=<int>` returns
  `list[MarketplaceSkillResponse]`, requires `manage_builtin_skills`, and lists
  active Marketplace sources without requiring `marketplace:read`.
- `GET /api/skills/?include_builtin=true&skip=<int>&limit=<int>&q=<str>&tags=<str>`
  returns `UserSkillListResponse` with personal entries first and projected
  Builtins after them. The default `include_builtin=false` preserves existing
  API callers.
- `GET /api/skills/{name}` and
  `GET /api/skills/{name}/files/{path}` may return a role-visible Builtin only
  when no persisted personal Skill owns `name`.
- `PATCH /api/skills/{name}/toggle` updates the Builtin preference when the
  resolved source is Builtin-only.

### 3. Contracts

- Projected entries set `is_builtin=true`, `installed_from="builtin"`, expose
  central file paths/content for reading, and derive `enabled` from
  `disabled_builtin_skill_names`.
- Search and tag filters run against both personal and Builtin metadata;
  pagination and counts are computed after the two sources are merged.
- The effective load quota remains bounded by `SKILL_EFFECTIVE_LOAD_LIMIT`.
  Persisted personal names, including disabled names, shadow Builtins before
  the remaining Builtin quota is filled.
- If a personal Skill owns a name but lacks a requested file, return `404`;
  never fall through to the same-name Builtin file.
- The Skills UI exposes a read-only file viewer for Builtins. It may expose the
  independent enable/disable action, but must hide edit, delete, upload,
  publish, pin/favorite, selection, and save controls.
- ZIP, GitHub, and Marketplace install/create paths reject a role-visible
  Builtin-only name instead of creating a writable personal shadow.

### 4. Validation & Error Matrix

| Condition | Expected behavior |
| --- | --- |
| Admin has `manage_builtin_skills` but not `marketplace:read` | Admin Marketplace source list succeeds |
| Builtin storage or role resolution fails during list projection | Log a warning and return personal Skills |
| Name is owned by a persisted personal Skill | Personal detail/file/write behavior wins |
| Personal name exists but requested file is absent | `404`; do not read the Builtin file |
| Builtin-only detail or file read | Succeeds when role-visible |
| Builtin-only edit/delete/binary upload/preference/publish | `403` read-only error |
| Builtin-only toggle | Update `disabled_builtin_skill_names`; do not modify `disabled_skills` |
| ZIP creation conflicts with a Builtin | Preview marks it existing; upload reports a read-only conflict (aggregate request may return `400`) |
| Marketplace installation conflicts with a Builtin | `403` read-only error |
| GitHub installation conflicts with a Builtin | Per-Skill error; no personal files are created |

### 5. Good / Base / Bad Cases

- Good: an analyst sees an eligible Builtin in `/skills`, opens its files in
  read-only mode, and disables it without changing any personal preference.
- Base: a user has no eligible Builtins; list ordering, counts, actions, and
  response fields behave exactly like the personal-only catalog.
- Bad: a disabled personal `planner` exists and the API displays or reads the
  Builtin `planner`, or an import path creates a writable personal copy over a
  role-visible Builtin.

### 6. Tests Required

- `tests/api/test_builtin_skill_routes.py`: admin-scoped active Marketplace
  source listing.
- `tests/api/test_skill_routes.py`: merged list marker/order, Builtin detail and
  file reads, personal-file no-fallback, independent toggle preference, and
  Builtin write/import rejection.
- GitHub and Marketplace route tests: their install paths reject a
  role-visible Builtin before persisting personal files.
- `tests/infra/skill/test_storage_effective_skills.py`: role filtering,
  disabled Builtin projection, quota, and personal-name shadowing.
- Frontend API/component tests: `include_builtin=true`, Builtin source mapping,
  hidden write actions, and the read-only file viewer entry point.

### 7. Wrong vs Correct

```python
# Wrong: a missing personal file leaks content from a same-name Builtin.
content = await storage.get_skill_file(name, path, user_id)
if content is None:
    content = (await storage.get_builtin_skill_for_user(name, user_id))["files"].get(path)

# Correct: resolve ownership first; only Builtin-only names may fall back.
content = await storage.get_skill_file(name, path, user_id)
if content is None and not await storage.list_skill_file_paths(name, user_id):
    builtin = await storage.get_builtin_skill_for_user(name, user_id)
    content = (builtin or {}).get("files", {}).get(path)
```
