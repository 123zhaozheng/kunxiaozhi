# Technical Design

## Boundaries

- `src/api/routes/builtin_skill.py` owns the admin-only Marketplace source
  listing used by Builtin configuration.
- `src/infra/skill/marketplace.py` remains the only reader of Marketplace
  metadata and files.
- `src/infra/skill/storage.py` owns the effective user-visible Skill catalog,
  including role filtering, same-name shadowing, independent Builtin disabled
  preferences, and Builtin file reads.
- `src/api/routes/skill.py` exposes the catalog and read-only Builtin file
  access while rejecting all Builtin writes.
- `frontend/src/components/panels/BuiltinSkillsPanel.tsx` consumes the admin
  source endpoint. The personal Skills card renders Builtin entries with
  read-only actions.

## Data Flow

### Admin Marketplace selector

```text
BuiltinSkillsPanel
  -> builtinSkillApi.listMarketplace()
  -> GET /api/admin/builtin-skills/marketplace
  -> require_permissions("manage_builtin_skills")
  -> MarketplaceStorage.list_marketplace_skills(active_only=True)
```

The endpoint must not depend on `marketplace:read`; it is part of the admin
Builtin management capability. It returns the existing
`MarketplaceSkillResponse` shape and only active entries.

### User effective Skill catalog

1. Load persisted personal Skills using the existing filters and preference
   metadata.
2. Resolve the user's roles and `skill:admin` access, then load active Builtin
   names eligible for those roles.
3. Remove names owned by any persisted personal Skill, including disabled
   personal Skills.
4. Load Builtin files from `skill_builtin_files` without copying them to
   `skill_files`.
5. Mark projected entries with `is_builtin=true`, `installed_from="builtin"`,
   and `enabled` derived from `disabled_builtin_skill_names`.
6. Merge personal entries before Builtin entries, apply search/tag filtering,
   counts, and page slicing.

## Contracts

- `GET /api/admin/builtin-skills/marketplace?skip=&limit=` ->
  `list[MarketplaceSkillResponse]`.
- `UserSkill` gains `is_builtin: bool = false`; frontend `SkillSource` gains
  `"builtin"`.
- `GET /api/skills/` returns the merged effective catalog. Builtin entries are
  readable through the existing `GET /api/skills/{name}` and
  `GET /api/skills/{name}/files/{path}` routes.
- Builtin-only `PUT`, `DELETE`, binary upload, preference, publish, and user
  creation paths return `403`.
- `PATCH /api/skills/{name}/toggle` writes
  `disabled_builtin_skill_names` for Builtin-only names and continues to use
  `disabled_skills` for persisted personal names.

## Compatibility and Failure Handling

- Existing personal Skill response fields and ordering remain unchanged for
  users with no eligible Builtin Skills.
- A Builtin lookup failure logs a warning and returns the personal catalog; it
  must not block chat or personal Skill management.
- The admin Marketplace selector shows a request error instead of treating a
  failed request as an empty Marketplace.
- Same-name personal Skills always win for reads, writes, and list projection.

## Trade-offs

- The personal Skills endpoint now represents an effective, read-mostly view,
  so `total` includes visible Builtin entries. This is preferable to a second
  page that hides what runtime injection actually loads.
- Builtin metadata is derived from central files at read time rather than
  duplicated in user storage, preserving global updates and read-only safety.
