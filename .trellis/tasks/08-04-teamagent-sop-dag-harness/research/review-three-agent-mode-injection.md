# Research: Three-agent mode injection comparison

- Query: Compare FastAgent, SearchAgent, and TeamAgent prompt, middleware, tools, subagent, harness profile, sandbox, skills, memory, and env injection stacks; identify unintended cross-mode leakage, duplicates, ordering hazards, and intentional differences, with emphasis on current uncommitted TeamAgent changes.
- Scope: mixed (internal code + installed deepagents behavior)
- Date: 2026-08-07

## Findings

### Compact comparison

| Stack | FastAgent | SearchAgent | TeamAgent (explicit team) |
|---|---|---|---|
| Base/backend | `FAST_SYSTEM_PROMPT`; persistent backend, no sandbox (`src/agents/fast_agent/nodes.py:133-143`) | persistent or sandbox backend; sandbox prompt/runtime selected by `_create_backend_and_prompt` (`src/agents/search_agent/nodes.py:135-151`) | persistent or sandbox backend; explicit team prepends `SEARCH_SANDBOX_SYSTEM_PROMPT` to router prompt when sandbox is active (`src/agents/team_agent/nodes.py:268-324`) |
| Context tools | Fast context adds human/reveal/transfer, internal tools with `include_sandbox_tools=False`, memory tools; skills are loaded (`src/agents/fast_agent/context.py:161-247`) | Search context adds same base tools, internal sandbox tools, memory, and `upload_url_to_sandbox` when enabled (`src/agents/search_agent/context.py:178-264`) | Reuses Fast setup, then removes management/interaction names; appends `upload_url_to_sandbox` when enabled (`src/agents/team_agent/context.py:24-47`) |
| Main prompt sections | Full workflow (`MAIN_AGENT_PROMPT_SECTIONS`) + persona + skills + memory + goal (`src/agents/fast_agent/nodes.py:208-246`) | Same, plus sandbox capability/runtime, marketplace, persona-skill harness, goal (`src/agents/search_agent/nodes.py:257-313`) | Explicit team keeps only safety + `task` guide, then adds sandbox capability/runtime, marketplace, goal, and SOP guidance (`src/agents/team_agent/nodes.py:515-554`) |
| Main middleware order | retry → binary → sections → memory index → deferred search → code interpreter → goal rubric → cache (`src/agents/fast_agent/nodes.py:208-246`) | retry → MCP quota → binary → sections → sandbox MCP/env → memory index → deferred search → code interpreter → goal rubric → cache (`src/agents/search_agent/nodes.py:257-313`) | retry → binary → router sections → sandbox MCP/env → memory index → deferred search → goal rubric → cache → TeamToolExclusion → optional SOP guard (`src/agents/team_agent/nodes.py:515-591`) |
| Subagent stack | retry → binary → activity log → persona/skills/memory → deferred search → cache (`src/agents/fast_agent/nodes.py:173-195`) | Same plus capability/runtime/marketplace/persona-skill and env prompt (`src/agents/search_agent/nodes.py:205-244`) | Role sections + capability/runtime/marketplace + env, deferred search, cache, TeamToolExclusion (`src/agents/team_agent/nodes.py:380-409`, `431-480`) |
| Harness | Shared compact_zh profile; short Todo is retained (`src/agents/core/persona.py:85-103`, `src/agents/core/harness_prompt_overrides.py:158-166`) | Same | Temporary profile excludes `TodoListMiddleware` and its short subclass during graph assembly (`src/agents/team_agent/harness_profile.py:23-64`) |
| Memory/env | Memory tools/guide; no env or sandbox middleware | Memory tools/guide/index; env keys and sandbox MCP prompts only with sandbox | Memory tools/guide/index; env and sandbox MCP prompts still attached to the router when sandbox is active |

### Severity-ranked findings

1. **High — Team router still receives execution/filesystem tools despite the “pure router” contract.**

   `TeamAgentContext` only removes a small name set (`ask_human`, marketplace, persona/team management) and deliberately retains `reveal_*`, transfers, and `read_document` (`src/agents/team_agent/context.py:9-20`, `src/agents/team_agent/context.py:31-47`; test expectations at `tests/agents/test_team_context_sandbox_tools.py:126-147`). `create_deep_agent` is still given a backend and `filtered_tools` with no Team-specific built-in tool exclusion (`src/agents/team_agent/nodes.py:593-606`). The installed `deepagents==0.6.7` then always adds `FilesystemMiddleware` and inherits caller tools for subagents (`.venv/Lib/site-packages/deepagents/graph.py:710-735`, `633-645`). `TeamToolExclusionMiddleware` removes only `write_todos` (`src/agents/team_agent/tool_exclusion.py:69-86`). Therefore the router model can see/use `ls/read_file/write_file/edit_file/glob/grep/execute` plus retained direct tools, contradicting the explicit “do not execute/read/create/modify/deliver directly” prompt (`src/agents/team_agent/prompt.py:9-20`, `src/agents/team_agent/nodes.py:520-526`).

2. **High — `update_sop` is marked main-agent-only but is inherited by role subagents.**

   The SOP tool is appended to the parent `filtered_tools` with a comment saying it is “主代理专属” (`src/agents/team_agent/nodes.py:346-363`). Role specs omit a `tools` key (`src/agents/team_agent/nodes.py:467-480`), and deepagents 0.6.7 explicitly inherits parent tools when a subagent does not declare its own (`.venv/Lib/site-packages/deepagents/graph.py:633-645`). Role middleware only excludes `write_todos`, not `update_sop` (`src/agents/team_agent/nodes.py:380-409`). This lets role members mutate the SOP plan / trigger approval flow even though the dispatch guard is intentionally mounted only on the main router (`src/agents/team_agent/sop/guard.py:1-5`, `src/agents/team_agent/nodes.py:582-591`).

3. **Medium — Sandbox env/MCP prompt injection leaks into the Team router.**

   The Team main stack attaches `SandboxMCPMiddleware` and `EnvVarPromptMiddleware` whenever `sandbox_backend` exists (`src/agents/team_agent/nodes.py:555-559`). These append dynamic sandbox service and environment-key sections to the model-visible system message (`src/infra/agent/middleware/prompt_injection.py:107-160`). This is intentional for SearchAgent, but conflicts with Team’s stated router-only prompt split (`src/agents/team_agent/nodes.py:520-526`) and increases the router’s knowledge of execution infrastructure even if tool visibility is later tightened.

4. **Medium — Sandbox prompt/runtime content is duplicated across Team’s base prompt and middleware sections.**

   In explicit team + sandbox mode, `SEARCH_SANDBOX_SYSTEM_PROMPT` is prepended directly to `system_prompt` (`src/agents/team_agent/nodes.py:305-313`), while capability/runtime sections are also appended to the Team router’s `SectionPromptMiddleware` (`src/agents/team_agent/nodes.py:539-544`). SearchAgent keeps its sandbox base prompt in its backend/prompt selection and separately adds capability/runtime sections (`src/agents/search_agent/nodes.py:135-151`, `257-277`), so the Team path has a larger chance of repeated storage/work_dir guidance. The runtime section itself is also supplied to each role subagent (`src/agents/team_agent/nodes.py:414-449`).

5. **Low — Team’s context-level upload tool gate is broader than actual backend availability.**

   `TeamAgentContext.setup()` appends `upload_url_to_sandbox` solely from `settings.ENABLE_SANDBOX` (`src/agents/team_agent/context.py:42-47`), before `team_router_node` has successfully created a sandbox backend (`src/agents/team_agent/nodes.py:268-324`). SearchAgent uses the same setting-based gate (`src/agents/search_agent/context.py:229-234`), so this is an intentional cross-mode convention, but Team fallback/error paths can carry a sandbox-only tool until graph construction fails or filters it.

### Intentional differences confirmed

- FastAgent intentionally has no sandbox tools/prompts and passes `include_sandbox_tools=settings.ENABLE_SANDBOX and self.agent_id != "fast"` (`src/agents/fast_agent/context.py:188-194`); its node has no sandbox/env/MCP prompt middleware.
- SearchAgent is the reference sandbox stack: capability text before work_dir, then marketplace/persona-skill prompt, sandbox MCP/env middleware, memory/deferred tools, and cache (`src/agents/search_agent/nodes.py:207-244`, `257-313`).
- Team intentionally strips management/interaction tools and uses a temporary harness profile to remove both Todo middleware classes (`src/agents/team_agent/context.py:9-20`, `src/agents/team_agent/harness_profile.py:23-64`).
- Shared compact_zh localization preserves rendered dynamic `task` descriptions rather than restoring `{available_agents}` (`src/agents/core/harness_prompt_overrides.py:186-207`), and the same profile is registered for Fast/Search while Team adds only temporary exclusions (`src/agents/core/persona.py:85-103`, `src/agents/team_agent/harness_profile.py:31-64`).

## External references

- Installed `deepagents==0.6.7`, inspected `.venv/Lib/site-packages/deepagents/graph.py` and `middleware/subagents.py`: `create_deep_agent` adds filesystem/subagent scaffolding; custom subagents inherit parent tools unless `tools` is explicitly supplied.
- Trellis/backend contract: `.trellis/spec/backend/agent-harness.md` (compact_zh, exact schema preservation, dynamic task description) and sandbox prompt contract in `.trellis/spec/backend/sandbox-providers.md` (capability section before runtime section; Fast has no sandbox path).

## Related specs

- `.trellis/spec/backend/agent-harness.md`
- `.trellis/spec/backend/sandbox-providers.md`
- `.trellis/spec/backend/marketplace-sandbox-skills.md`
- `.trellis/spec/backend/builtin-skills.md`

## Caveats / Not Found

- This was read-only source comparison; no graph runtime snapshot was built. The two High findings are directly supported by deepagents 0.6.7 source and current Team call sites, but a model-provider-specific request trace would still be useful to confirm exact final tool ordering.
- Existing Team tests verify tool-name pruning and Todo exclusion, but do not assert that router built-ins are absent or that `update_sop` is absent from role subagent tool lists (`tests/agents/test_team_context_sandbox_tools.py`, `tests/agents/test_team_harness_profile.py`, `tests/agents/test_team_tool_exclusion_middleware.py`).
