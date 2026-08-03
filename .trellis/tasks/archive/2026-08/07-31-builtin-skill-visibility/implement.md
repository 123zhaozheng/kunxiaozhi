# Implementation Plan

1. Update backend and frontend Skill contracts with the Builtin marker/source.
2. Add the admin Builtin Marketplace listing endpoint, client method, panel
   error state, and route-level tests.
3. Add SkillStorage helpers for role-visible Builtin catalog entries, disabled
   Builtin preferences, same-name shadowing, and central file reads.
4. Update `/api/skills/` list/detail/file routes to merge/read Builtins and
   reject Builtin-only writes; add API and storage regression tests.
5. Update SkillCard and related action plumbing so Builtins are visibly
   read-only, while the enable/disable action targets the independent Builtin
   preference.
6. Refresh backend/frontend specs if the final contract differs from the
   current Builtin Skill spec.
7. Run focused backend/frontend tests, lint, build, and full relevant checks.
8. Review the diff, archive the task, and commit the completed fix.

## Validation Commands

```powershell
uv run pytest tests/api/test_builtin_skill_routes.py tests/api/test_skill_routes.py tests/infra/skill/test_builtin_storage.py tests/infra/skill/test_storage_list_user_skills.py
pnpm --dir frontend test -- --run
pnpm --dir frontend lint
pnpm --dir frontend build
uv run ruff check src
git diff --check
```

## Risk / Rollback Points

- Keep Marketplace source data and Builtin storage separate; rollback can
  remove only the new endpoint and projection code without changing persisted
  personal Skills.
- Preserve the existing runtime `get_effective_skills()` contract while adding
  the user-facing catalog path.
- Do not migrate or copy existing Builtin files into user documents.
