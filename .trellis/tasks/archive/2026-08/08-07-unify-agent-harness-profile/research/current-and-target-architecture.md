# Research: Current and Target Agent Harness Architecture

- Query: Map the current compact_zh / HarnessProfile architecture and define the simplest single-authority Chinese concise harness, including Team tool exclusion and deepagents 0.6.7 constraints.
- Scope: mixed (internal code/specs plus installed deepagents source/API)
- Date: 2026-08-07

## Findings

### Current authority is split across four layers

1. `src/agents/core/harness_prompt_overrides.py` is the catalog and model-view layer. `HarnessCatalog` stores behavior, write_todos, memory, tool descriptions, field annotations, filesystem/execute/task replacements, and the localized available-agent heading (lines 27-37). `ZH_CATALOG` is the singleton (lines 113-136), `_ZH_TOOLS` and `_ZH_FIELDS` are the current compact_zh names/concepts (lines 45-111), and `localize_tool_for_model()` deep-copies only known `BaseTool` schemas while leaving runtime/validation originals untouched (lines 186-207). Unknown or non-`BaseTool` tools pass through unchanged (lines 187-193).

2. The same module owns two extra middleware concepts: a `ShortTodoListMiddleware` subclass (lines 139-166) and `HarnessLocalizationMiddleware` (lines 252-281). `build_harness_extra_middleware()` always returns both, with no mode argument (lines 284-285). The localizer rewrites vendor system prompt constants and model-visible tool copies, but preserves the already-rendered `task` description so the actual subagent list is not lost (lines 194-202, 211-249). This is the important dynamic-schema boundary: descriptions/titles may change, machine names/properties/required/types/enums/defaults must not.

3. `src/agents/core/persona.py` is currently the registration authority. It imports the catalog/middleware (lines 19-23), dynamically discovers deepagents APIs (lines 33-43), sets `_BEHAVIOR_GUIDE = COMPACT_ZH_BEHAVIOR_GUIDE` (lines 83-83), builds one shared `HarnessProfile` with `base_system_prompt`, all tool overrides, localization+ShortTodo middleware, and `TodoListMiddleware` exclusion (lines 85-100), then registers only provider keys `anthropic`, `openai`, and `google_genai` (lines 101-103). Importing persona is therefore a side effect required to boot compact_zh; Fast/Search/Team nodes import `build_persona_prompt_sections` from persona (Fast line 23, Search line 24, Team line 23), which incidentally registers the profile.

4. `src/agents/core/subagent_prompts.py` is a second prompt authority, not merely a catalog consumer. It imports `ZH_CATALOG` for memory (line 12), appends the localized `SUBAGENT_TASK_GUIDE` to `MAIN_AGENT_PROMPT_SECTIONS` (lines 73-80), and builds default/detailed subagent prompts from localized workflow, safety, file delivery, tool discovery, and handoff sections (lines 103-125). These strings preserve non-language contracts: `Current task start time: ...`, relative-date interpretation, `reveal_file`/`reveal_project`, transfer limits, untrusted-content rules, `search_tools`/`execute`/`mcporter`, and structured handoff fields.

5. Team has a mode-specific assembly authority in `src/agents/team_agent/harness_profile.py`. It obtains both exact middleware classes, `TodoListMiddleware` and the dynamically-created ShortTodo subclass (lines 16-28), then builds a profile containing only `excluded_middleware` (lines 31-35). `team_harness_profile()` resolves `provider:identifier`, snapshots private `deepagents.profiles.harness.harness_profiles._HARNESS_PROFILES`, registers additively, yields around synchronous `create_deep_agent`, and restores the private map afterward (lines 38-65). This works because graph assembly is synchronous, but it is coupled to private deepagents internals and global mutable registry state.

6. Team also has a request-layer fallback in `src/agents/team_agent/tool_exclusion.py`. `TeamToolExclusionMiddleware` removes `write_todos` from `ModelRequest.tools` and strips both `## `write_todos`` and legacy `## write_todos` sections from string or block system messages (lines 22-86), with sync/async wrappers (lines 88-100). Team nodes append it to every role-subagent middleware stack (Team `nodes.py:380-409`) and to the main stack (`nodes.py:575-581`), then still wrap `create_deep_agent` in `team_harness_profile` (`nodes.py:593-607`). Thus Team currently has duplicated exclusion mechanisms: assembly-time exact-type removal plus request-time tool/prompt stripping.

### create_deep_agent call-site behavior

- Fast: `src/agents/fast_agent/nodes.py:248-258` calls `create_deep_agent` with model, system prompt, backend, tools, custom synchronous subagent(s), and user middleware. No explicit profile context exists; compact_zh comes from the provider profile registered as a persona import side effect. Its custom subagent middleware is assembled separately (`nodes.py:173-204`) and includes its own prompt/cache/activity middleware.
- Search: `src/agents/search_agent/nodes.py:315-325` has the same implicit profile path. It adds sandbox/MCP/skill prompt sections and custom subagent middleware (`nodes.py:205-244`, `246+`), but does not add a localizer explicitly.
- Team: `src/agents/team_agent/nodes.py:597-607` is the only call site using a profile context. Main and role subagent stacks receive TeamToolExclusion; deepagents' profile extra middleware is also inherited by declarative synchronous subagents, so the Team profile affects the main and role stacks during assembly.

### Installed deepagents 0.6.7 profile API and constraints

Installed package: `.venv/Lib/site-packages/deepagents`, version `0.6.7` (`deepagents.__version__`). Relevant source is `.venv/Lib/site-packages/deepagents/profiles/harness/harness_profiles.py` and `deepagents/graph.py`.

- `HarnessProfile` fields are `base_system_prompt`, `system_prompt_suffix`, `tool_description_overrides`, `excluded_tools`, `excluded_middleware`, `extra_middleware`, and `general_purpose_subagent` (class declaration at installed `harness_profiles.py:484`; signature verified with `inspect.signature`).
- `register_harness_profile()` is additive: existing and incoming profiles are merged (`harness_profiles.py:955-969`, public API `972+`). Tool and middleware exclusion sets union; mapping entries merge with incoming values winning; extra middleware merges by type (`harness_profiles.py:1185-1240`). Re-registering the same provider key cannot be treated as replacement.
- Lookup for pre-built models is canonical `provider:identifier` first, then identifier-only only when it already contains `:`, then provider fallback (installed `harness_profiles.py:1243+`; behavior verified via `_harness_profile_for_model`). Provider-level registrations therefore resolve for the three adapters currently registered by persona; any new adapter/provider requires explicit registration or compact_zh silently falls back to vendor English.
- `create_deep_agent()` resolves the profile before assembly (`graph.py:537-543`). Profile extra middleware is appended to the general-purpose stack at `graph.py:652-686` and main stack at `graph.py:749-776`; excluded middleware is applied after all tool-injecting/user/profile middleware. Profile coverage is verified globally (`graph.py:777-787`).
- `excluded_middleware` matches exact class or exact public middleware name, rejects protected scaffolding such as `FilesystemMiddleware` and `SubAgentMiddleware`, and raises when an entry matches nothing. This explains why Team must list both the vendor `TodoListMiddleware` and the custom ShortTodo subclass; subclass matching is not used.
- `extra_middleware` applies to main, auto general-purpose, and declarative synchronous subagent stacks, but not `CompiledSubAgent` or remote `AsyncSubAgent` stacks. Fast/Search/Team custom subagent specs are synchronous declarative/compiled mixtures, so this distinction must be preserved when unifying behavior.
- `excluded_tools={"write_todos"}` is a supported profile-level mechanism, but it runs after tool-injecting middleware and does not remove the middleware's system prompt section. If Team needs the section absent as well, assembly-time middleware exclusion or the existing section-stripping fallback remains necessary.

### Compact_zh names and concepts that must remain

Current catalog tool names in `_ZH_TOOLS` (`harness_prompt_overrides.py:45-77`) include core deepagents tools: `task`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `execute`, `search_tools`, `write_todos`; memory tools: `memory_retain`, `memory_recall`, `memory_delete`; document/media/sandbox tools: `read_document`, `dify_kb_retrieve`, `audio_transcribe`, `upload_url_to_sandbox`; marketplace/env/MCP tools: `find_skills`, `install_skill`, `env_var_list`, `env_var_set`, `env_var_delete`, `env_var_delete_all`, `sandbox_mcp_add`, `sandbox_mcp_update`, `sandbox_mcp_remove`; image/persona/team tools: `image_generate`, `create_persona_preset`, `update_persona_preset`, `search_persona_presets`, `create_agent_team`.

Field annotations in `_ZH_FIELDS` (`harness_prompt_overrides.py:79-110`) must remain additive only. Names such as `file_path`, `old_string`, `new_string`, `replace_all`, `output_mode`, `query`, `subagent_type`, `todos`, `status`, `dify_kb_retrieve.top_k`, `score_threshold`, sandbox `env_keys`, and persona/team IDs are machine contracts and cannot be translated. The `task` description must retain `{available_agents}` until `SubAgentMiddleware` renders it; after rendering, the localizer must preserve the actual agent list and never restore the literal placeholder.

Behavioral concepts in `COMPACT_ZH_BEHAVIOR_GUIDE`, `ZH_CATALOG.write_todos_system`, `ZH_CATALOG.task_system`, memory guide, and subagent prompts must remain: concise direct answers; evidence-first conflict handling; safe defaults/ask only when ambiguity blocks progress; closed-loop read/execute/verify; explicit blockers; exactly one `in_progress` todo and no parallel todo calls in modes where write_todos is enabled; task delegation only for isolated complex work; full context/expected output/current-task timestamp; parent verification and integration of subagent results; file-read-before-edit and absolute-path rules; reveal/transfer boundaries; untrusted content and secret-safety rules; and structured handoff notes.

### Recommended target architecture: one canonical profile factory/catalog, thin mode adapters

The simplest migration is to make one core module (preferably `src/agents/core/harness_prompt_overrides.py`, or a new adjacent `harness.py` that owns it) the sole harness authority:

1. Keep `ZH_CATALOG`, schema localization, vendor prompt replacement, `ShortTodoListMiddleware`, and `HarnessLocalizationMiddleware` together. Add a single `build_compact_zh_profile(*, team: bool = False)` (or two explicit profile builders sharing one immutable base) that returns the complete profile. The base profile owns all Chinese prompt/tool/schema behavior; the Team variant adds only exact-type todo exclusions.
2. Move provider registration into the same core authority. Register the base profile once for every supported provider; do not let `persona.py` own harness side effects. Keep `persona.py` focused on persona identity and preferred-agent resolution. If compatibility requires import-time registration, expose an explicit `ensure_compact_zh_harness_registered()` and call it from one agent bootstrap path; retain a harmless compatibility import temporarily while migrating tests.
3. Collapse Team's temporary profile construction into the canonical builder. `team_harness_profile.py` can remain a thin compatibility wrapper around `build_compact_zh_profile(team=True)`, but no longer reconstruct `HarnessProfile` independently or know the ShortTodo implementation details. Preserve the synchronous save/register/restore boundary only if deepagents 0.6.7 offers no per-call profile parameter.
4. Keep `TeamToolExclusionMiddleware` only as a defensive fallback for unresolved model keys, already-compiled subagents, and future deepagents assembly changes. Its implementation should consume the same canonical `WRITE_TODOS_TOOL_NAME`/section-heading constants from the core harness module, preventing string drift. Do not remove it in the first migration because the profile registry is global and private-map restoration is inherently fallible.
5. Treat `subagent_prompts.py` as content consumers rather than a second harness registry: continue to own domain-specific workflow/handoff sections, but source common task/memory/tool guidance from the canonical catalog. Avoid moving runtime persona/team/business prompts into the harness; only shared deepagents behavior belongs there.
6. Keep all three create_deep_agent call sites unchanged initially. They already import persona in Fast/Search/Team and thus exercise the shared profile. After centralizing registration, add a bootstrap/import test proving Fast, Search, and Team each resolve the same base profile for Anthropic/OpenAI/Google models; separately test Team's mode profile and compiled-subagent fallback.

This target keeps one Chinese language source and one schema-localization implementation while preserving the only unavoidable mode distinction: Team's SOP replaces write_todos, so Team must exclude it at assembly time. It avoids a larger call-site refactor and respects deepagents' lack of a public per-call HarnessProfile argument in 0.6.7.

### Evidence-backed migration boundary

Safe first boundary:

- Move `_profile_kwargs`, provider-key iteration, and registration guard from `persona.py:85-103` to the canonical harness module.
- Export `build_compact_zh_profile(team=False)`, `TEAM_EXCLUDED_MIDDLEWARE` (or a private builder), and shared write_todos section/name constants from that module.
- Refactor `team_agent/harness_profile.py` to call the builder and keep only model-key context management/private registry compatibility.
- Refactor `tool_exclusion.py` to import shared constants, not duplicate headings/name literals.
- Leave `subagent_prompts.py` and Fast/Search/Team middleware lists intact, then update tests around one registration authority and all three provider resolutions.

Do not cross this boundary in the same change:

- Do not translate machine tool/field names or alter original Pydantic schemas.
- Do not remove the `task` dynamic placeholder or replace a rendered description with catalog text.
- Do not remove Team request-layer filtering until unresolved-model, compiled-subagent, and profile-coverage tests exist.
- Do not register arbitrary provider aliases without confirming `get_model_provider()` output; a provider mismatch silently returns an empty/default profile.
- Do not mutate global profile state across an `await`; the current context manager is safe only because `create_deep_agent()` assembly is synchronous.

## Caveats / Not Found

- No public deepagents 0.6.7 API was found for passing a HarnessProfile directly to one `create_deep_agent()` invocation; current mode-specific behavior necessarily uses registry lookup/context mutation or request middleware.
- Team's `team_harness_profile()` accesses private `_HARNESS_PROFILES` and `_ensure_harness_profiles_loaded`; this is the highest upgrade risk if deepagents changes registry internals. The installed API documents registration as beta.
- The repository's PowerShell display renders many Chinese source strings as mojibake, but tests and UTF-8 file reads show the intended compact_zh content and identifiers; migration should preserve bytes/encoding and rely on contract tests rather than visual terminal output.
- Provider registrations currently cover only `anthropic`, `openai`, and `google_genai`; no `openrouter`, Azure, or other adapter registration was found in the harness code. Verify actual runtime provider names before adding keys.
- `CompiledSubAgent` middleware is pre-built and does not receive profile `extra_middleware`; any promise of universal localization must explicitly cover compiled graphs or keep their own middleware construction unchanged.

## Related Specs

- `.trellis/spec/backend/agent-harness.md` — compact_zh catalog singleton, model-view schema invariants, dynamic `task` description, vendor prompt hash tests, import boundary, and required regression tests.
- `.trellis/spec/backend/persona-preferred-agent.md` — persona is identity; fast/search/team are capability templates; keep harness concerns separate from `preferred_agent_id` resolution.
- `.trellis/spec/backend/persona-runtime-and-dify-kb.md` — confirms tool exposure/scope and `agent_options` are separate concerns; do not mix persona runtime wiring into harness localization.

## External / Version References

- Installed package `.venv/Lib/site-packages/deepagents` version `0.6.7`; API/source references above are from the local installed distribution (`HarnessProfile` declaration around `profiles/harness/harness_profiles.py:484`, registration/merge around `:955-1240`, profile resolution around `:1243+`, and graph assembly around `graph.py:537-787`).
- `deepagents.HarnessProfile` and `register_harness_profile` signatures were inspected directly: profile registration is provider-wide (`"openai"`) or exact (`"openai:model"`); no per-call profile argument exists on `create_deep_agent`.
