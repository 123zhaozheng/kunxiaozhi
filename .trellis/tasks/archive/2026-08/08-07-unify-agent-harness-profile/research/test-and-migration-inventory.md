# Research: Harness profile test and migration inventory

- Query: Inventory tests, specs, and callers affected by removing `compact_zh` as a mode concept and consolidating Todo/harness behavior into one authoritative concise Chinese harness with per-agent capability switches.
- Scope: internal / mixed (dependency version and runtime API inspected locally)
- Date: 2026-08-07

## Findings

### Current contracts and affected implementation

| Area | Files / code pattern | Migration impact and acceptance requirement |
|---|---|---|
| Harness catalog/localization authority | `src/agents/core/harness_prompt_overrides.py:27-37,113-136,186-207,252-285` defines `HarnessCatalog`, `ZH_CATALOG`, copied model-view tools, schema annotation stripping, dynamic `task` preservation, and `build_harness_extra_middleware()` | Keep one catalog and one localization middleware as the only model-visible Chinese source. Renaming/removing `compact_zh` must not reintroduce mode arguments or duplicate descriptions. Preserve tool names, fields, required/default/enum/type schema; compare schemas after recursively removing only `description`/`title`. Unknown/deferred tools must pass through unchanged. |
| Todo construction | `src/agents/core/harness_prompt_overrides.py:139-166` creates a subclass of LangChain `TodoListMiddleware`; `:158-166` builds one Todo middleware with catalog-owned system/tool descriptions | Fast and Search must retain exactly one concise Todo middleware and `write_todos`. The authoritative description must contain exactly-one-`in_progress` and no-parallel constraints. Do not make Todo presence a localization mode; make it an agent capability switch. |
| Shared profile registration | `src/agents/core/persona.py:83-103` registers one profile under `anthropic`, `openai`, and `google_genai`, excludes base `TodoListMiddleware`, and adds extra middleware through a callable | Consolidation should keep provider/model resolution independent of agent identity. If profile registration changes, preserve all currently supported provider adapters and test that every resolved adapter receives the common Chinese base/localizer. Avoid importing agent configuration from `src.infra.tool` (the spec calls this an infra→agents→infra cycle). |
| Team override | `src/agents/team_agent/harness_profile.py:23-35,38-65` builds a temporary model-level profile excluding both exact classes (`TodoListMiddleware` and generated `ShortTodoListMiddleware`) and save/restores the registry around synchronous `create_deep_agent` assembly | Team must omit Todo from the assembled stack, including the inherited short subclass. Profile key must be derived using installed deepagents `get_model_provider`/`get_model_identifier`; unresolved keys must fail safe without mutating global registry. Restore the prior profile on context exit, including nested/exception paths. |
| Team fallback/request guard | `src/agents/team_agent/tool_exclusion.py:22-100`; `src/agents/team_agent/nodes.py:407-408,580-607` | Keep request-layer fallback for unknown model keys, differing subagent models, and deepagents assembly changes. It must remove both `write_todos` tool and `## `write_todos``/legacy heading sections while preserving all other sections. Team's official replacement is SOP (`update_sop`), so no Todo UI event should be expected for Team. |
| Fast graph caller | `src/agents/fast_agent/nodes.py:176-204,206-258` builds subagent/user middleware and calls `create_deep_agent` without Team profile | Fast inherits shared harness and must retain Todo; capability-specific additions (sandbox/code interpreter/etc.) remain in node middleware. Add a graph-level regression that final model request includes `write_todos` and concise Todo guidance. |
| Search graph caller | `src/agents/search_agent/nodes.py:222-253,255-325` builds analogous subagent/user middleware and calls `create_deep_agent` | Search also retains Todo. Verify deferred `search_tools`, sandbox prompts, persona/skills sections, and Todo can coexist; do not accidentally apply Team exclusion to Search. |
| Team graph caller | `src/agents/team_agent/nodes.py:380-409,515-607` adds `TeamToolExclusionMiddleware` to main and subagent stacks, then wraps `create_deep_agent` in `team_harness_profile(llm)` | Team capability switch must be explicit and centralized. Ensure both main and role subagents omit Todo, while `task` dispatch and SOP tools remain available. The no-team fallback path should be covered separately because it still uses Team nodes but may be expected to behave as a normal single-agent capability set. |
| Final Todo rendering | `src/infra/agent/events/tool_events.py:80-122` intercepts `write_todos` starts/ends and emits `present_todo`; `src/infra/writer/presenter_events.py:230-247` emits `todo:updated` | Fast/Search Todo calls should continue rendering `todo:updated` with `todos`, depth, and `agent_id`; normal tool start/end events remain suppressed for `write_todos`. Team should never emit Todo because the tool is absent, but this event handler remains backward-compatible for old traces/replayed events. |

### Existing test matrix

| Test | Current assertions | Required migration/update |
|---|---|---|
| `tests/agents/core/test_harness_prompt_overrides.py:45-58` | Catalog marker/size and Chinese guidance; `task` retains `{available_agents}` | Rename wording away from mode-specific `compact_zh`, but retain dense Chinese/size checks and template-only placeholder check. |
| `tests/agents/core/test_harness_prompt_overrides.py:60-79` | Short subclass survives exclusion of base class; Todo tool/system strings share catalog and enforce one in-progress/no parallel | Convert to authoritative-harness capability test. Keep exact-type exclusion proof; add Fast/Search enabled and Team disabled cases. |
| `tests/agents/core/test_harness_prompt_overrides.py:82-105` | Real `SubAgentMiddleware` renders agent list, then localizer preserves rendered `task` and removes literal placeholder | Must remain unchanged in substance; this is the regression preventing localization from restoring `{available_agents}`. |
| `tests/agents/core/test_harness_prompt_overrides.py:108-130` | SHA-256 pins vendor filesystem/execute/task prompt sources | Keep hashes/version boundary. Dependency copy changes must fail visibly and trigger replacement review. |
| `tests/agents/core/test_harness_prompt_overrides.py:133-180` | Recursive schema equality after removing only annotations; known `search_tools` localization; extras/unknown tool pass-through | Keep as hard acceptance. Include all newly model-visible built-ins in catalog/field map. |
| `tests/agents/core/test_harness_prompt_overrides.py:182-220` | Original Pydantic schema still enforces required/default/enums; localized payload is materially smaller | Preserve runtime-original validation and compactness threshold. Do not invoke localized copies for execution. |
| `tests/agents/core/test_harness_prompt_overrides.py:223-268` | Extra middleware is Todo + localizer; system replacement and runtime-tool identity behavior | Replace count/order assertions only if implementation intentionally wraps a single consolidated middleware; still require Todo capability output and localizer output, and ensure originals are not mutated. |
| `tests/agents/core/test_harness_prompt_overrides.py:271-289` | Anthropic/OpenAI/Google models resolve common profile; two extra middleware entries | Expand to all supported provider/model key shapes used by `LLMClient`; assert per-agent capability switch does not alter common localization. |
| `tests/agents/core/test_harness_prompt_overrides.py:291-312` | Fresh subprocess imports persona/harness and checks behavior plus short Fast prompt | Retain fresh-process smoke test to catch import-order and registration side effects. |
| `tests/agents/core/test_harness_prompt_overrides.py:314-322` | Direct-first `src.infra.tool.deferred_manager` import succeeds | Retain as import-boundary guard; do not make infra import the agent catalog. |
| `tests/agents/test_team_harness_profile.py:20-85` | Team profile excludes both exact Todo classes, restores prior profile, normal profile keeps short Todo, and deepagent assembly succeeds | Split/rename `test_normal_mode_keeps_short_todo` to explicit Fast/Search capability test. Keep Team registry restore and exact-class coverage; add assertions that Team final request has no `write_todos`/Todo section. |
| `tests/agents/test_team_tool_exclusion_middleware.py:56-137` | Compact and legacy headings, string/block system messages, no-op identity, forwarding, and section preservation | Keep as fallback compatibility tests even if assembly-time exclusion becomes primary. This protects unknown model keys, mixed-model subagents, and dependency changes. |
| `tests/agents/core/test_subagent_prompts.py:1-217` | Stable handoff/workflow/file/memory contracts, Fast/Search prompt size and capability identifiers, fresh-process Chinese prompt contract | Remove only assertions that call Chinese localization a selectable mode; preserve all workflow/tool-routing contracts and short base prompts. |
| `tests/agents/test_team_agent_sandbox_support.py` and `tests/agents/test_team_agent_sop_tool_hook.py` | Shims for `HarnessProfile`/registration and captured Team `create_deep_agent` kwargs/middleware | Update shim expectations if profile constructor/registration API changes; retain sandbox/SOP assertions and add Todo absence to captured Team middleware where appropriate. |
| `tests/agents/test_disabled_skills_config_propagation.py:192-243,490-534,725-819` | Captures Fast/Search/Team graph middleware, prompt sections, and cache ordering | Existing tests are broad caller regressions. Add capability assertions without disturbing prompt-cache-last checks; ensure Team exclusion does not remove non-Todo sections. |
| `tests/unit/agents/test_existing_agents_regression.py` | Import smoke coverage for deepagents modules | Run with fresh-process harness smoke after dependency/profile changes. |

### Provider/model handling and compatibility

- Project requirements are `deepagents>=0.5.3` (`pyproject.toml:21`), with the lock currently resolving `deepagents==0.6.7` (`uv.lock:954-967`), `langchain==1.3.2`, `langchain-core==1.4.0`, `langchain-openai==1.2.2`, `langchain-anthropic==1.4.4`, and `langchain-google-genai==4.2.4`.
- Installed `HarnessProfile` supports `base_system_prompt`, `tool_description_overrides`, `excluded_middleware`, and `extra_middleware` (callable or sequence). Installed `_harness_profile_for_model` first resolves `provider:identifier`, then identifier/provider fallback; bare model identifiers are not registry-matched for pre-built models. This is why the Team profile must use the installed provider+identifier helpers and must not assume an OpenAI-only key.
- Shared registration currently uses provider keys (`anthropic`, `openai`, `google_genai`), while Team uses model-specific canonical keys. A consolidated implementation should preserve both provider-wide localization and model-specific capability overlays, with additive registration isolated to a synchronous context around graph assembly.
- Rollback requirement: if profile resolution/assembly fails, restore the exact previous registry entry; if no key can be resolved, leave the registry untouched and retain request-layer Team filtering. Reverting the change must leave old `compact_zh` imports or aliases available only if external callers/tests still depend on them; do not keep a second behavioral implementation.

### Acceptance recommendations

1. Make the concise Chinese catalog/profile authoritative and mode-free; expose capability switches such as `todo_enabled`/agent capability policy rather than `compact_zh` vs Todo modes.
2. Test a matrix of `(fast, search, team) × (OpenAI, Anthropic, Google, pre-built/custom model)` for profile resolution, tool presence, and final system/tool localization.
3. For Fast/Search, assert final assembled/request-visible tools include `write_todos`, catalog description, one-in-progress/no-parallel guidance, and `todo:updated` rendering path.
4. For Team, assert final assembled/request-visible tools exclude `write_todos`, no `## `write_todos`` section survives, `task` remains dynamically rendered, SOP remains available, and non-Todo tools/sections are unchanged.
5. Keep schema equality recursive and annotation-only, preserve original Pydantic validation, and test unknown/deferred tools as identity pass-through.
6. Keep vendor prompt hashes, fresh-process boot, direct-first infra import, profile save/restore, and request-layer fallback tests as rollback guards.

### Validation commands

```powershell
python -m pytest tests/agents/core/test_harness_prompt_overrides.py -q
python -m pytest tests/agents/core/test_subagent_prompts.py tests/agents/test_team_harness_profile.py tests/agents/test_team_tool_exclusion_middleware.py -q
python -m pytest tests/agents/test_team_agent_sandbox_support.py tests/agents/test_team_agent_sop_tool_hook.py tests/agents/test_disabled_skills_config_propagation.py -q
python -m pytest tests/unit/agents/test_existing_agents_regression.py -q
python -c "import src.infra.tool.deferred_manager"
python -c "import src.agents.core.persona; from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model; print('harness booted')"
```

## Related specs

- `.trellis/spec/backend/agent-harness.md` — compact Chinese model-view localization, dynamic `task`, Todo constraints, schema equality, vendor hash/import tests.
- `.trellis/spec/backend/persona-preferred-agent.md` — `fast`/`search`/`team` are capability templates, not localization modes; preserve preferred-agent routing.
- `.trellis/spec/backend/quality-guidelines.md` — backend validation expectations.

## Caveats / Not Found

- No dedicated test currently exercises `AgentEventProcessor` Todo rendering directly; `src/infra/agent/events/tool_events.py:90-122` is covered only indirectly by the presence of the tool. Add a focused event test if final Todo rendering is part of acceptance.
- Current task `prd.md` is still TBD and no `design.md`/`implement.md` exists, so capability names and whether legacy symbol aliases are required remain decisions for the parent planning phase.
- The repository contains mojibake-rendered Chinese source/test strings in the current terminal output; substring assertions are existing contracts, but migration should avoid broad textual rewrites that alter unrelated prompt content.
