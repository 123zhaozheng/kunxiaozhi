# Execution Plan

1. Audit symbols, imports, task directories, and changed files.
2. Remove confirmed stale test fakes, naming/doc remnants, and unused imports.
3. Update backend specs and task artifacts.
4. Run focused regression tests, full relevant lint/type/build checks, and
   `git diff --check`.
5. Archive the task and create the final commit.

Validation:

- `uv run pytest tests/persona_preset tests/agents tests/api/test_builtin_skill_routes.py tests/infra/skill tests/infra/backend -q`
- `uv run ruff check src`
- `pnpm lint` and `pnpm build` from `frontend/`
