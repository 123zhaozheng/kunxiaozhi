# PRD: Compress agent harness prompts and tool schemas

## Problem

Every agent turn built by `create_deep_agent` ships oversized **vendor** tool descriptions (`write_todos` ~3.9k, `task` ~6.5k, `read_file` ~1.9k, `execute` ~2.8k) plus redundant **first-party** system sections (behavior guide, reveal gate, task usage guide). This burns tokens and weakens prompt-cache density without improving product behavior.

## Goals

1. Cut always-on **tool schema** prose for deepagents/langchain tools via `HarnessProfile.tool_description_overrides` (+ short `TodoListMiddleware` where overrides cannot reach).
2. Compress first-party **HARD** system playbooks without dropping hard gates (reveal, safety, task timestamp handoff, cache block order).
3. Keep **cache architecture** intact: block 0 global-stable; session blocks separate; volatile only at tail (`<memory_index>`, deferred MCP list).
4. Prove with tests/char budgets that critical phrases remain and sizes drop.
5. Add a high-density Simplified Chinese harness experiment covering all
   always-on English system guidance and schema descriptions.
6. Make both the Chinese experiment and the entire harness-compression task
   explicitly reversible.

## Non-goals

- Removing tools or changing tool APIs.
- Changing deferred-MCP stub limits (already good).
- Merging volatile sections into stable prefix.
- Disabling `write_todos` / `task` unless product later asks.
- Translating protocol identifiers that are part of tool-call contracts:
  tool names, argument/property names, enum values, provider keys, and
  machine-parsed sentinel headings.
- Translating dynamic third-party or deferred-MCP schemas in this phase.

## Chinese harness experiment

- Translate and aggressively condense human-readable system prompt text,
  tool descriptions, and argument/property descriptions into Simplified
  Chinese.
- Keep tool names, JSON-schema property names, enum literals, provider keys,
  and other machine-consumed identifiers unchanged.
- Apply the same selected harness variant to Anthropic, OpenAI-compatible,
  and Google model adapters.
- Measure the final serialized request surfaces, not only source constants:
  system-message blocks plus the complete `tools` array.
- Compare compact English and compact Chinese by characters, model-specific
  token counts where available, and tool-call correctness smoke tests.
- The target and production-default mode is compact Simplified Chinese.
  Compact English exists as the immediate rollback mode, not as the default.

## Rollback requirements

1. **Experiment rollback:** switch from compact Chinese back to compact
   English without reverting code.
2. **Task rollback:** preserve a documented, tested route back to the
   pre-compression harness behavior. The implementation must avoid scattering
   language/mode conditionals across prompt modules.
3. Rollback must preserve tool APIs and stored data; it may require a process
   restart because harness profiles register at import time.

## Acceptance criteria

- [ ] `HarnessProfile` registration supplies short descriptions for at least: `task`, `read_file`, `execute` (and ideally other FS tools if safe).
- [ ] `write_todos` tool description and its system section are substantially shorter than langchain defaults (target ≤ ~600 chars tool + ≤ ~400 chars system, or better).
- [ ] First-party `_BEHAVIOR_GUIDE`, `FILE_REVEAL_GUIDE`, `SUBAGENT_TASK_GUIDE` compressed without losing: reveal required, artifact gate, synthesize/handoff, `Current task start time` English field, search_tools/deferred rules.
- [ ] Existing tests in `tests/agents/core/test_subagent_prompts.py` and prompt-caching middleware tests still pass (update only if intentional copy changes require it).
- [ ] New or extended tests assert char budgets / presence of critical phrases and that profile overrides are registered.
- [ ] No work_dir / persona / memory_index content moved into global base.
- [ ] Compact Chinese covers all always-on human-readable English text in the
      assembled base/tool harness; an allowlist documents intentionally stable
      English identifiers and sentinel labels.
- [ ] Anthropic, OpenAI-compatible, and Google resolve the same selected core
      harness variant.
- [ ] A request-level measurement fixture reports system chars/tokens and tool
      schema chars/tokens for compact English vs compact Chinese.
- [ ] Experiment rollback to compact English and full-task rollback behavior
      are both tested and documented.
- [ ] Description-only schema localization preserves tool names, property and
      required sets, enums, defaults, types, and runtime validation behavior.

## Constraints

- Must use deepagents 0.6.x `HarnessProfile` (additive `register_harness_profile`).
- `TodoListMiddleware` is **not** scaffolding → may exclude + re-add with short prompts; `FilesystemMiddleware` / `SubAgentMiddleware` are scaffolding → only override descriptions, do not exclude.
- `task` override **must** keep `{available_agents}` placeholder.
- Surgical diffs only; no drive-by refactors.
- Translation must increase information density without changing tool names,
  schema property names, enum values, or required English handoff labels such
  as `Current task start time`.
- Runtime selection is resolved once during process startup so one request
  cannot mix harness languages.
- Localized schemas are model-call views only; original `BaseTool` instances
  and Pydantic models remain the execution and validation source of truth.

## Harness mode decision

- Use one startup setting with `legacy | compact_en | compact_zh`.
- Default to `compact_zh`.
- `compact_en` is the no-code experiment rollback.
- `legacy` restores the pre-compression harness behavior for full-task
  rollback.

## Out of scope (later)

- Chinese UI reply-language policy as product copy.
- ask_human / memory_retain docstring slim-down (P1 follow-up).
