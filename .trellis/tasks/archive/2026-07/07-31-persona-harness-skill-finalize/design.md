# Technical Design

## Boundaries

- `src/kernel/schemas/persona_preset.py`: authoritative persisted Persona contract.
- `src/infra/persona_preset/manager.py` and `skill_harness.py`: resolve Marketplace metadata into non-persistent runtime hints.
- `src/infra/skill/storage.py`, `builtin.py`, and the effective-source helper: merge user and Builtin reads while preserving user precedence.
- API routes and frontend services/components expose the same visibility and read-only rules.

## Data flow

Persona create/update -> validate active Marketplace names -> persist names only. Persona use -> load current Marketplace `SKILL.md` descriptions -> build `skill_hints` -> pass `persona_skill_hints` to Search Agent. No user `skill_files` writes and no Persona overlay.

User Skill reads/list/prompt/transfer -> effective source resolver(user first, then role-eligible Builtin). User Skill writes -> resolve source and reject Builtin-only names with `permission_denied`; same-name user writes remain ordinary user operations.

## Compatibility

Legacy persisted Persona documents may still contain removed fields; readers ignore them unless needed to construct the supported snapshot. Old Marketplace install endpoints keep their existing response/error contract.

## Risks and rollback

The highest-risk boundary is removing stale materialization while preserving manual Marketplace installation. Changes are staged by layer and validated with focused tests before broad cleanup. If Harness resolution fails, degrade to Persona prompt-only behavior; if Builtin projection fails, return user Skills without Builtin injection.
