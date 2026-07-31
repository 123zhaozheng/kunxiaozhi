# Unified Persona Harness + Builtin Skill finalization

## Goal

Finish the latest architecture already partially implemented in the working tree: Persona stores Marketplace Skill names as runtime hints for Search Agent Harness; Builtin Skills remain admin-managed, role-scoped, read-only projections; ordinary user Skills remain the authoritative writable source. Remove the superseded Persona-time persistent materialization and idempotent-create/publication flow, then make backend, frontend, tests, and task records agree on one contract.

## Confirmed facts

- The current worktree already contains Harness schema/runtime changes, Builtin Skill storage/routes/UI, and tests, but also retains stale materialization and publication/idempotency changes.
- `.trellis/spec/backend/persona-marketplace-skills.md` and the latest architecture task define exact-name Harness hints, no Persona overlay, no Persona-time persistent copy, and no `create_request_id` flow.
- `SkillStorage.get_effective_skills()` currently merges Builtin data directly; the latest design requires a shared effective-source projection so list, prompt, read, transfer, and write authorization agree.
- Persona Skill selection must be active Marketplace names only; any selected Skill forces Search and Team is rejected.

## Requirements

1. Reconcile Persona schemas, manager, API, frontend editor, and tests with the latest Harness contract.
2. Remove stale Persona materialization/publication/idempotency paths and restore ordinary Marketplace install semantics.
3. Complete Builtin Skill effective-source behavior, visibility, role filtering, preference isolation, and read-only write rejection across API and agent paths.
4. Preserve existing unrelated work and compatibility for old persisted documents where the latest contract explicitly allows it.
5. Clean up overlapping Trellis task records after implementation: retain this task as the integration record, archive completed superseded tasks, and remove empty planning-only duplicates.

## Acceptance criteria

- [ ] Focused backend and frontend tests pass for Persona Harness, Builtin Skills, Marketplace install, effective skill storage, and agent transfer/read paths.
- [ ] `rg` finds no stale runtime use of `persona_marketplace_skills`, `ensure_marketplace_skill_installed`, `create_request_id`, or Persona publication preflight except in explicitly retained compatibility/spec text.
- [ ] Persona with Skills persists only exact Marketplace names, forces Search, emits hints without installing to user storage, and silently degrades when a dependency later disappears.
- [ ] Builtin Skills are visible only when role-eligible, shadowed by same-name user Skills, read-only in every backend write path, and preferences remain isolated from user Skill preferences.
- [ ] Full relevant lint/type/test commands run and failures are either fixed or documented.
- [ ] Trellis task tree contains one authoritative completed integration task and no duplicate active planning tasks for the superseded approaches.

## Out of scope

- Historical migration of old Team Personas.
- New product capabilities unrelated to Persona Harness or Builtin Skills.
- Reverting unrelated WeCom, branding, or agent-harness changes.
