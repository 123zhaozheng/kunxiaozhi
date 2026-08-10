# Research: TeamAgent write_todos removal

- Query: Why TeamAgent previously removed/filtered the `write_todos` tool but still exposed its system-prompt guidance; whether the current Team harness profile removes both tool and prompt for the main agent and role subagents while preserving Fast/Search behavior.
- Scope: mixed (internal code/tests + installed DeepAgents/LangChain implementation)
- Date: 2026-08-07

## Findings

The old compact_zh provider profile excluded only the vendor base `TodoListMiddleware` (`src/agents/core/persona.py:92-100`), but also materialized `build_harness_extra_middleware()` (`src/agents/core/harness_prompt_overrides.py:158-166,284-285`). That factory creates a distinct `ShortTodoListMiddleware` subclass (`harness_prompt_overrides.py:151-155`) and supplies the localized `write_todos_system` prompt plus tool (`harness_prompt_overrides.py:161-165`). In DeepAgents 0.6.7, `_apply_excluded_middleware` matches class entries by exact `type`, not `isinstance` (`.venv/Lib/site-packages/deepagents/_excluded_middleware.py:97-105,137-150`), so excluding the base class never removed the subclass instance.

There are two independent model-visible channels: middleware tools are assembled from each middleware's `.tools`, while `TodoListMiddleware.awrap_model_call` appends its `system_prompt` to the outgoing `SystemMessage`. Therefore filtering a request's tool name cannot remove text already/also injected by the middleware. The fallback `TeamToolExclusionMiddleware` strips the current request's `write_todos` tool and heading sections (`src/agents/team_agent/tool_exclusion.py:28-42,45-86`), but it is user middleware. DeepAgents places user middleware before harness `extra_middleware` (`.venv/Lib/site-packages/deepagents/graph.py:749-756`), and LangChain documents first-in-list as the outermost layer (`.venv/Lib/site-packages/langchain/agents/middleware/types.py:503-504,597-598`). Thus TeamToolExclusion can strip an existing section, then the later ShortTodo middleware appends the localized `write_todos` guidance again. The earlier filter was consequently order-sensitive and could not guarantee prompt removal.

The current fix constructs a model-scoped Team `HarnessProfile` whose `TEAM_EXCLUDED_MIDDLEWARE` contains both exact classes (`src/agents/team_agent/harness_profile.py:23-35`). `team_harness_profile(llm)` temporarily installs it around `create_deep_agent` (`harness_profile.py:39-64`; `src/agents/team_agent/nodes.py:593-607`). DeepAgents assembles the base `TodoListMiddleware`, user middleware, and profile extras, then applies exclusions to the fully assembled main stack (`.venv/Lib/site-packages/deepagents/graph.py:710-776`). This removes both TodoList and ShortTodo before either can run, so neither the `write_todos` tool nor its prompt section is injected. `TeamToolExclusionMiddleware` remains as a runtime fallback (`nodes.py:579-581`) rather than the primary removal mechanism.

The same profile is resolved for declarative role subagents: each role spec omits `model`, so DeepAgents inherits the parent model (`.venv/Lib/site-packages/deepagents/graph.py:575-582`), builds `TodoListMiddleware`, appends the role's middleware, appends profile extras, and applies the same exact-type exclusions (`graph.py:586-622`). Team role specs explicitly retain `TeamToolExclusionMiddleware` (`src/agents/team_agent/nodes.py:380-409,467-480`), providing defense in depth. Under the normal resolved-model Team path, both Team main and role subagent stacks therefore lose the tool and prompt before runtime.

Fast/Search are preserved because they do not enter `team_harness_profile`; their normal compact_zh provider profile still materializes ShortTodo (`tests/agents/test_team_harness_profile.py:44-52`). The targeted regression tests pass: `python -m pytest -q tests/agents/test_team_harness_profile.py tests/agents/test_team_tool_exclusion_middleware.py` => 10 passed.

## Related specs

- `.trellis/spec/backend/agent-harness.md:1-6,18-32` — compact_zh harness contract, shared `write_todos` catalog, and model-visible prompt/schema rules.
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/prd.md` and `design.md` — Team SOP replacement and harness-removal decisions.

## Caveats / Not Found

- The current tests prove exact-class profile coverage and main-stack assembly, plus standalone request filtering, but do not invoke a real role subagent model request and assert its final `SystemMessage`; the role conclusion is from the DeepAgents assembly path and Team role-spec construction.
- `team_harness_profile` intentionally skips installation if provider/identifier resolution fails (`harness_profile.py:46-52`). In that rare fallback (or if a future role explicitly selects a different/unregistered model), TeamToolExclusion alone remains order-sensitive and may not remove a later ShortTodo prompt. Normal ChatOpenAI/Anthropic/Google model paths resolve and are covered by the current design.
- No git history was used; historical behavior is reconstructed from current code comments, task artifacts, tests, and installed dependency source.
