# Implementation Plan

## 1. Shared Marketplace publication service

- [ ] Extract reusable publish/update logic from `src/api/routes/skill.py` into the Skill/Marketplace service layer.
- [ ] Preserve current single-Skill publish endpoint behavior and tests.
- [ ] Add batch preflight for local ownership, file completeness, existing publication mapping, global name ownership, active state, and permissions.
- [ ] Add coordinated batch publication with compensation for records newly created during a failed Persona save.
- [ ] Verify global unique-name conflicts never overwrite Marketplace metadata or files.

Validation:

```powershell
uv run pytest tests/api/test_skill_routes.py tests/infra/skill/test_marketplace_storage.py -q
```

## 2. Persona schema, manager, and API

- [ ] Add `PersonaMarketplaceSkillRef` and `marketplace_skills` to create/update/response/snapshot types.
- [ ] Add structured publication preflight endpoint and response schemas.
- [ ] Add explicit publication confirmation to public Persona create/update.
- [ ] Reject public publication with unresolved, inactive, incomplete, or conflicting dependencies.
- [ ] Populate Marketplace references only after coordinated publication succeeds.
- [ ] Implement historical public `skill_names` compatibility without consumer-local fallback.
- [ ] Preserve private Persona local `skill_names` behavior.
- [ ] Preserve/correct dependency references when copying a public Persona.

Validation:

```powershell
uv run pytest tests/persona_preset tests/api/test_persona_preset_routes.py -q
```

## 3. Read-only Persona Skill runtime overlay

- [ ] Add shared Marketplace dependency resolver returning prompt descriptors and bounded file maps.
- [ ] Add a read-only `/skills/` overlay that routes Persona dependency names to Marketplace storage and all other names to consumer Skill storage.
- [ ] Implement list/read/search/glob behavior and reject writes to mounted Marketplace dependencies.
- [ ] Detect consumer-local conflicts: allow only matching Marketplace installation metadata; reject manual/unknown same-name Skills.
- [ ] Ensure no consumer Skill document/meta/cache entry is created during activation or chat.
- [ ] Keep `install_skill -> sandbox/work_dir/temp_skills` code untouched.

Validation:

```powershell
uv run pytest tests/infra/backend/test_skills_store_backend.py tests/infra/tool/test_skill_marketplace_tool.py -q
```

## 4. Agent and task propagation

- [ ] Carry Marketplace dependency refs through chat request resolution, session metadata, task submission, executor, queue payload, recovery, and retry.
- [ ] Merge resolved descriptors into Fast/Search/Team `context.skills`.
- [ ] Attach the read-only overlay backend for Fast/Search/Team.
- [ ] Ensure `build_skills_prompt()` includes temporary Skill descriptions.
- [ ] Verify clearing/switching Persona replaces the dependency set and recovery reconstructs it from refs.
- [ ] Verify public dependency failures are blocking, not silently converted to `missing_skill_names`.

Validation:

```powershell
uv run pytest tests/agents tests/infra/task tests/api/routes/test_chat_team_validation.py -q
```

## 5. Persona editor and activation UX

- [ ] Add frontend types/API for publication preflight and structured dependency errors.
- [ ] Add blocking Marketplace name-conflict dialog.
- [ ] Add confirmation dialog listing personal Skills that will become public/readable/installable.
- [ ] Retry/submit public Persona save only after explicit confirmation.
- [ ] Show exact conflicting/unavailable Skill names when Persona activation fails.
- [ ] Remove the misleading success-only path for partial dependencies.
- [ ] Add localized copy and component/service tests.

Validation:

```powershell
Set-Location frontend
pnpm test
pnpm exec tsc --noEmit
pnpm exec eslint .
```

## 6. Compatibility and full quality gate

- [ ] Test historical public Persona with only `skill_names`.
- [ ] Test public Persona with unpublished local Skills, already-published Skills, mixed batch, conflicting Marketplace name, inactive dependency, and partial publish failure.
- [ ] Test consumer with no same-name Skill, matching Marketplace install, conflicting manual Skill, and source metadata missing.
- [ ] Test runtime read-only behavior and absence of persistent consumer writes.
- [ ] Run existing sandbox Marketplace temporary-install tests unchanged.
- [ ] Record the existing `test_list_persona_presets_returns_real_total` Mongo mock defect separately if still present; do not hide new regressions behind it.

Full validation:

```powershell
uv run ruff check src tests
uv run mypy src
uv run pytest tests/persona_preset tests/api/test_persona_preset_routes.py tests/api/test_skill_routes.py tests/infra/backend/test_skills_store_backend.py tests/infra/skill tests/infra/tool/test_skill_marketplace_tool.py tests/agents tests/infra/task -q
Set-Location frontend
pnpm test
pnpm exec tsc --noEmit
pnpm exec eslint .
```

## Risk and rollback points

- Publication compensation is the highest-risk write path; verify newly created versus pre-existing Marketplace records before any rollback delete.
- `/skills/` routing is shared by all agents; land overlay tests before wiring contexts.
- Session/task propagation must preserve empty versus absent dependency lists during recovery.
- Do not modify `src/infra/tool/skill_marketplace_tool.py` behavior except test fixtures needed to prove non-regression.
