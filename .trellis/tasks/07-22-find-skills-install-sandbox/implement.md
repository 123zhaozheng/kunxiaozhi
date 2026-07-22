# Implementation checklist

- [x] Add `find_skills` and `install_skill` internal tools.
- [x] Preserve multi-keyword ANY-word marketplace search.
- [x] Resolve the concrete sandbox backend's real `work_dir`.
- [x] Materialize S3 binary references as original bytes.
- [x] Stage uploads, validate all responses/files, and finalize atomically.
- [x] Define idempotence by readable `SKILL.md`.
- [x] Gate runtime tools by agent sandbox capability; exclude FastAgent.
- [x] Derive Search/Team prompt guidance from final policy-filtered tools.
- [x] Revert unrelated prompt/tool localization edits.
- [x] Add regression tests for work_dir, binary bytes, partial failure/retry, completeness, policy gating, prompt gating, and FastAgent exclusion.
- [x] Run Ruff, Mypy, targeted tests, and diff checks.

## Validation

```text
uv run ruff check <changed Python files>
uv run mypy src/infra/tool/skill_marketplace_tool.py src/infra/tool/internal_registry.py src/infra/skill/marketplace.py src/agents/fast_agent/context.py src/agents/search_agent/context.py
uv run pytest tests/infra/tool/test_skill_marketplace_tool.py tests/infra/tool/test_env_var_tool.py tests/test_mcp_tool_policies.py tests/test_sandbox_mcp_prompt_guidance.py tests/infra/skill/test_marketplace_storage.py -q --tb=short
git diff --check
```

Current expanded result: 233 passed.
