# Fix Builtin Skill Marketplace Visibility and User Skill Listing

## Goal

Make Builtin Skill administration and user-facing Skill discovery reflect the
same source of truth as runtime injection. An administrator who can manage
Builtin Skills must be able to choose an existing Marketplace Skill, and a user
must be able to see role-eligible Builtin Skills in the local Skills space
without receiving a writable copy.

## Confirmed Facts

- `BuiltinSkillsPanel` loads Marketplace choices through `marketplaceApi.list()`.
- `GET /api/marketplace/` requires `marketplace:read`; an administrator with
  only `manage_builtin_skills` receives an authorization error, which the panel
  currently converts to an empty list.
- `GET /api/skills/` calls `SkillStorage.list_user_skills()`, which only reads
  the user's persisted `skill_files` documents.
- Runtime injection already exists in `SkillStorage.get_effective_skills()` and
  applies role filtering, disabled Builtin preferences, same-name user Skill
  shadowing, and a bounded load quota.
- Builtin files are stored centrally and must remain read-only from user Skill
  write paths.

## Requirements

1. Add an admin-scoped Marketplace listing path for Builtin configuration. It
   must use the existing Marketplace metadata/file store, return active skills
   visible to the administrator, and require only `manage_builtin_skills`.
2. Use that path in the Builtin configuration panel and preserve the existing
   create-from-Marketplace request contract.
3. Extend the user Skill listing contract to include role-eligible effective
   Builtin Skills alongside persisted personal Skills. Personal Skills remain
   first; a disabled personal Skill shadows a same-name Builtin.
4. Expose an explicit read-only Builtin marker in the API and frontend model.
   Builtin entries must show their files/description but must not expose edit,
   delete, upload, publish, or writable file actions.
5. Keep Builtin enable/disable preferences independent from
   `disabled_skills`, using `disabled_builtin_skill_names`.
6. Preserve pagination, search, tags, counts, and graceful degradation when
   Builtin storage or role resolution fails.

## Acceptance Criteria

- [x] An admin with `manage_builtin_skills` but without `marketplace:read` sees
  active Marketplace Skills in the Builtin source selector.
- [x] Marketplace listing failures are surfaced as an error state rather than
  silently rendered as "no skills".
- [x] A user with an eligible role sees the injected Builtin Skill in `/skills`
  with a Builtin/read-only indicator and can open its files.
- [x] A same-name persisted personal Skill is the only displayed source; the
  Builtin is not duplicated.
- [x] Builtin-only edit/delete/file-write/publish attempts remain rejected, and
  the UI does not offer those actions.
- [x] Builtin disabled preferences affect the displayed effective list without
  changing personal Skill enabled state.
- [x] Existing personal Skill list behavior remains intact when no Builtin is
  eligible or Builtin loading fails.
- [x] Backend and frontend regression tests cover both reported defects and
  `pnpm lint`, `pnpm build`, `uv run ruff check src` pass.

## Out of Scope

- Copying Builtin files into each user's persisted Skill storage.
- Changing Marketplace publication/install semantics.
- Allowing users to edit or republish Builtin content.
