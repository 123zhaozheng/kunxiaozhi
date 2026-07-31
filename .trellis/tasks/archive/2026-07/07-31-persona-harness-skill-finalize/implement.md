# Implementation Plan

1. Inventory stale symbols and run focused Persona/Builtin/Marketplace tests.
2. Read applicable backend/frontend specs and patch Persona contract/manager/API/frontend to the latest Harness semantics.
3. Restore Marketplace manual install path and remove stale materialization/idempotency/publication code and tests.
4. Finish Builtin effective-source resolver, role filtering, preference isolation, and read-only guards across all read/write paths.
5. Run focused tests, then lint/type checks and broader regression tests.
6. Update the executable spec with any newly learned invariants.
7. Archive superseded Trellis tasks and complete this integration task only after verification.

Validation commands:

- `uv run pytest tests/persona_preset tests/api/test_persona_preset_routes.py tests/infra/skill tests/api/test_builtin_skill_routes.py tests/infra/backend tests/agents -q`
- `pnpm --dir frontend lint`
- `pnpm --dir frontend type-check`
- `pnpm --dir frontend test`
- `uv run ruff check src tests`
