# Research: DeepAgents prompt pipeline for Fast/Search/Team agents

- Query: Trace the final model-visible system prompt for FastAgent, SearchAgent, and TeamAgent; separate `create_deep_agent(system_prompt=...)`, DeepAgents built-ins/HarnessProfile, and repository middleware; determine whether middleware is the main injection mechanism.
- Scope: mixed (repository + installed packages)
- Date: 2026-08-07

## Findings

### Assembly contract (installed `deepagents==0.6.7`)

- `create_deep_agent` accepts `system_prompt` and documents it as the caller prefix; DeepAgents then assembles `USER -> (BASE or HarnessProfile.base_system_prompt) -> HarnessProfile.system_prompt_suffix` ([`.venv/Lib/site-packages/deepagents/graph.py:112-141`](../../../.venv/Lib/site-packages/deepagents/graph.py:112), `:217-237`). The implementation is `base_prompt = _apply_profile_prompt(...)`, then `system_prompt + "\\n\\n" + base_prompt`, and finally passes that to LangChain `create_agent` ([`.../deepagents/graph.py:789-807`](../../../.venv/Lib/site-packages/deepagents/graph.py:789)).
- The DeepAgents built-in main stack is `TodoListMiddleware`, optional skills, filesystem, optional `SubAgentMiddleware`, summarization, patch-tool-call; repository `middleware=` is inserted after that base stack and before profile tail middleware ([`.../deepagents/graph.py:710-758`](../../../.venv/Lib/site-packages/deepagents/graph.py:710)). LangChain composes model-call middleware with the first list entry outermost ([`.../langchain/agents/factory.py:220-307`](../../../.venv/Lib/site-packages/langchain/agents/factory.py:220)); the model receives the resulting `request.system_message` as the first message ([`.../langchain/agents/factory.py:1289-1302`](../../../.venv/Lib/site-packages/langchain/agents/factory.py:1289)).
- Built-ins are genuine prompt injectors: filesystem appends its generated filesystem/execute guidance ([`.../deepagents/middleware/filesystem.py:1645-1664`](../../../.venv/Lib/site-packages/deepagents/middleware/filesystem.py:1645)); `SubAgentMiddleware` appends `task` instructions plus the rendered available-agent list ([`.../deepagents/middleware/subagents.py:704-711`](../../../.venv/Lib/site-packages/deepagents/middleware/subagents.py:704), `:769-789`); `TodoListMiddleware` appends write-todos guidance ([`.../langchain/agents/middleware/todo.py:231-256`](../../../.venv/Lib/site-packages/langchain/agents/middleware/todo.py:231)).

### FastAgent

- Caller/static prefix: `FAST_SYSTEM_PROMPT` is passed unchanged as `system_prompt` ([`src/agents/fast_agent/nodes.py:130-135`](../../../src/agents/fast_agent/nodes.py:130), `:248-258`); its text is the stable file/memory capability description ([`src/agents/fast_agent/prompt.py:6-8`](../../../src/agents/fast_agent/prompt.py:6)).
- Main dynamic content is repository middleware: one `SectionPromptMiddleware` receives `MAIN_AGENT_PROMPT_SECTIONS`, persona, skills, memory, and active-goal sections ([`src/agents/fast_agent/nodes.py:212-224`](../../../src/agents/fast_agent/nodes.py:212)); `MemoryIndexMiddleware`, tool-search, code-interpreter, rubric, and cache are added after it ([`.../fast_agent/nodes.py:225-246`](../../../src/agents/fast_agent/nodes.py:225)).
- Fast subagents use `SUBAGENT_PROMPT` as their declarative prefix and another `SectionPromptMiddleware` for persona/skills/memory; this is a separate subagent graph stack ([`src/agents/fast_agent/nodes.py:173-203`](../../../src/agents/fast_agent/nodes.py:173)).

### SearchAgent

- Caller/static prefix is the backend-selected `system_prompt` (sandbox or default) returned before graph assembly and passed to `create_deep_agent` ([`src/agents/search_agent/nodes.py:135-150`](../../../src/agents/search_agent/nodes.py:135), `:315-325`); source constants are `SANDBOX_SYSTEM_PROMPT` / `DEFAULT_SYSTEM_PROMPT` ([`src/agents/search_agent/prompt.py:7-14`](../../../src/agents/search_agent/prompt.py:7)).
- Repository `SectionPromptMiddleware` appends main workflow, persona, skills, memory, sandbox capability/runtime, marketplace, and goal sections ([`src/agents/search_agent/nodes.py:262-283`](../../../src/agents/search_agent/nodes.py:262)); sandbox-specific middleware then appends dynamic MCP descriptions and env-var key names ([`.../search_agent/nodes.py:284-289`](../../../src/agents/search_agent/nodes.py:284), [`src/infra/agent/middleware/prompt_injection.py:107-136`](../../../src/infra/agent/middleware/prompt_injection.py:107), `:139-160`).
- Search subagents likewise use `SUBAGENT_PROMPT` plus SectionPromptMiddleware and, in sandbox mode, EnvVarPromptMiddleware ([`src/agents/search_agent/nodes.py:212-252`](../../../src/agents/search_agent/nodes.py:212)).

### TeamAgent

- Explicit team main prefix is dynamically built router text (team roster, role summaries/instructions, default role, optional SOP clause) ([`src/agents/team_agent/nodes.py:240-265`](../../../src/agents/team_agent/nodes.py:240)); sandbox mode prepends the search sandbox static guidance ([`.../team_agent/nodes.py:310-313`](../../../src/agents/team_agent/nodes.py:310)). It is passed to `create_deep_agent` within `team_harness_profile` ([`.../team_agent/nodes.py:593-607`](../../../src/agents/team_agent/nodes.py:593)).
- Team main prompt sections are deliberately reduced to safety + task contract, then goal/sandbox/marketplace and SOP guidance are appended via one repository `SectionPromptMiddleware` ([`src/agents/team_agent/nodes.py:520-554`](../../../src/agents/team_agent/nodes.py:520)); sandbox EnvVar/MCP, memory, tool-search, rubric and guard middleware follow ([`.../team_agent/nodes.py:555-591`](../../../src/agents/team_agent/nodes.py:555)).
- Team role subagents receive `SUBAGENT_PROMPT` plus per-role `SectionPromptMiddleware` containing role persona, role/team instructions, skills, memory, and sandbox sections ([`src/agents/team_agent/nodes.py:431-480`](../../../src/agents/team_agent/nodes.py:431)).
- Shared compact-ZH `HarnessProfile` replaces DeepAgents' long `BASE_AGENT_PROMPT` with `COMPACT_ZH_BEHAVIOR_GUIDE`, adds short todo + localization middleware, and excludes the vendor Todo middleware ([`src/agents/core/persona.py:83-103`](../../../src/agents/core/persona.py:83), [`src/agents/core/harness_prompt_overrides.py:252-284`](../../../src/agents/core/harness_prompt_overrides.py:252)). Team temporarily adds exact-type exclusion for both vendor `TodoListMiddleware` and the short subclass, so Team has no write-todos prompt/tool injected by the main stack ([`src/agents/team_agent/harness_profile.py:23-35`](../../../src/agents/team_agent/harness_profile.py:23), [`.../harness_profile.py:54-64`](../../../src/agents/team_agent/harness_profile.py:54)). The profile overlay semantics are replacement of BASE, optional suffix, not replacement of caller `system_prompt` ([`.../deepagents/profiles/harness/harness_profiles.py:775-793`](../../../.venv/Lib/site-packages/deepagents/profiles/harness/harness_profiles.py:775)).

### Answer: is middleware the main injection mechanism?

Yes for the *final/effective* prompt, especially repository/session-specific content. `system_prompt=` contributes only the stable caller prefix; the profile contributes the compact behavior base; DeepAgents built-ins contribute filesystem/task/todo guidance; and most persona, skills, memory, sandbox, environment, goal, SOP, and tool-localization content is appended or rewritten at model-call time by middleware. Therefore middleware is the dominant injection mechanism for dynamic model-visible instructions, while `create_deep_agent(system_prompt=...)` remains the stable root prefix and not the sole (or even majority) source of the final prompt.

## Files found

- `src/agents/fast_agent/nodes.py` / `prompt.py` — Fast graph assembly, static prompt, and middleware sections.
- `src/agents/search_agent/nodes.py` / `prompt.py` — Search static prompt selection and sandbox/session injection.
- `src/agents/team_agent/nodes.py` / `prompt.py` / `harness_profile.py` — Team router, role subagents, SOP sections, and Todo exclusions.
- `src/infra/agent/middleware/prompt_injection.py` — Section, sandbox MCP, memory-index, and env-var model-call injection.
- `src/agents/core/persona.py` / `harness_prompt_overrides.py` — compact-ZH HarnessProfile and localization middleware.
- `.venv/Lib/site-packages/deepagents/graph.py`, `middleware/filesystem.py`, `middleware/subagents.py`, `profiles/harness/harness_profiles.py` — installed DeepAgents 0.6.7 assembly/profile/built-in behavior.
- `.venv/Lib/site-packages/langchain/agents/factory.py`, `middleware/todo.py` — installed LangChain middleware composition and todo prompt behavior.

## Related specs

- `.trellis/spec/backend/agent-harness.md` — compact_zh harness, dynamic `task` rendering, tool/schema localization, and profile/import boundaries.
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/design.md` — Team SOP prompt/harness decisions.

## Caveats / Not Found

- Exact final block order can vary with enabled features (sandbox, memory, deferred tools, rubric, model/provider profile) and whether the request is main agent or a subagent. The cited order is the construction order for the current installed versions.
- Team's temporary profile relies on the resolved `provider:identifier` key; if model identity cannot be resolved, the Team exclusion path is skipped (`src/agents/team_agent/harness_profile.py:46-51`).
