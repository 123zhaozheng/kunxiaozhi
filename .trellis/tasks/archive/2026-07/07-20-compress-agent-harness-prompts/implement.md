# Implement checklist

## 0. Reversible mode and catalogs

- [ ] Add typed startup setting `legacy | compact_en | compact_zh`; default
      `compact_zh` and reject invalid values.
- [ ] Centralize complete mode catalogs. Preserve exact pre-compression
      first-party strings and native vendor defaults for `legacy`; preserve
      compact-English strings for one-step rollback.
- [ ] Resolve the setting once before profile registration and log only the
      selected mode.

## 1. Vendor descriptions and schema views

- [ ] Keep `src/agents/core/harness_prompt_overrides.py` as the catalog/factory
      boundary for `write_todos`, `task`, `read_file`, `execute`, and any
      explicitly reviewed built-in FS tools.
- [ ] Preserve `{available_agents}` and all machine contract identifiers.
- [ ] Create model-call-only localized schema views for the curated built-ins;
      change only descriptions and pass unknown/MCP tools through unchanged.
- [ ] Keep original `BaseTool`/Pydantic objects for ToolNode execution and
      validation.

## 2. Register one complete profile

- [ ] Register the selected core profile for Anthropic, OpenAI-compatible, and
      Google provider keys.
- [ ] Preserve provider/model-specific additive suffix profiles.
- [ ] In `legacy`, reproduce the pre-compression middleware/tool behavior.

## 3. First-party guides

- [ ] Provide all three variants for `_BEHAVIOR_GUIDE`,
      `FILE_REVEAL_GUIDE`, and `SUBAGENT_TASK_GUIDE`.
- [ ] In compact Chinese, remove obvious prose while preserving reveal/artifact
      gates, safety, synthesis/handoff, deferred-tool routing, and exact
      `Current task start time`.
- [ ] Preserve stable/session/volatile block boundaries and middleware order.

## 4. Contract and mode tests

- [ ] Test provider x mode resolution in isolated processes because registration
      is import-time.
- [ ] Compare localized schemas after recursively removing `description`:
      property/required sets, enums, defaults, types, and additional-properties
      semantics must match originals.
- [ ] Smoke-test required, default, and enum inputs through original tool
      validation and dispatch.
- [ ] Assert `write_todos` remains available, `{available_agents}` formats, and
      unknown/deferred-MCP tools remain unchanged.
- [ ] Verify prompt-cache tool ordering, breakpoints, and cache annotations for
      localized views.

## 5. Request-level measurement

- [ ] Serialize representative fast/search/team requests for all three providers
      and modes; measure system and complete-tools chars/tokens separately.
- [ ] Scan compact Chinese for residual English with a checked-in contract
      allowlist.
- [ ] Add budgets against `legacy` and record explained exceptions.

## 6. Verification and rollback drill

- [ ] Run focused harness, subagent prompt, prompt-caching, config, lint, and
      type-check suites.
- [ ] Start separate processes in `compact_zh`, `compact_en`, and `legacy` and
      verify reported mode plus behavioral fixtures.
- [ ] Document the setting, restart requirement, comparison report, and rollback
      commands.
- [ ] Keep mode mechanism, catalogs/schema view, and default switch in isolated
      commits so the mechanism itself can be reverted safely.

## Validation commands

```bash
uv run pytest tests/agents/core/test_harness_prompt_overrides.py \
  tests/agents/core/test_subagent_prompts.py \
  tests/infra/agent/test_prompt_caching_middleware.py -q
uv run ruff check src tests
uv run mypy src
```
