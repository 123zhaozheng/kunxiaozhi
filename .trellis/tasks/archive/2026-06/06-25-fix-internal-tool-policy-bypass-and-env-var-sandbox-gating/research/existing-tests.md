# Research: Existing Tests Covering This Area

- **Query**: Find tests for internal_registry, tool loading, policy filtering, env_var tools. What's tested today? What gaps exist? Look in tests/infra/tool/, tests/agents/, tests/api/.
- **Scope**: internal
- **Date**: 2026-06-25

## Test files found

| File | Covers |
|---|---|
| `tests/test_mcp_tool_policies.py` | `MCPToolPolicy` schema, `MCPStorage.set_tool_policy`/`get_tool_policy` round-trip, `get_internal_tools_for_user` policy filtering, `get_internal_tool_infos` parameters, bulk policy loading |
| `tests/infra/tool/test_env_var_tool.py` | `get_env_var_tools` shape, `env_var_*` tool behavior, `env_var_prompt` builder + cache + middleware, **context.setup() env_var loading for FastAgent + SearchAgent** |
| `tests/api/test_mcp_routes.py` | Admin MCP routes incl. `kunxiaozhi_internal` server listing, internal tool discovery endpoint, tool toggle overflow |
| `tests/agents/test_team_agent_sandbox_support.py` | `ENABLE_SANDBOX` toggling for TeamAgent node backend selection (does NOT test env_var tool loading) |
| `tests/infra/tool/test_sandbox_mcp_rebuild.py` | `rebuild_sandbox_mcp` / `_sync_user_env_vars` / `build_env_flags` (env-var consumers) |
| `tests/infra/tool/test_persona_preset_tool.py` | persona_preset tool behavior |
| `tests/infra/tool/test_team_tool.py` | team tool behavior |
| `tests/infra/tool/test_image_generation_tool.py` | image_generate tool behavior |
| `tests/infra/tool/test_audio_transcribe_tool.py` | audio_transcribe tool behavior |

## What's tested today

### Policy filtering at the registry layer — `tests/test_mcp_tool_policies.py`

- `test_mcp_tool_policy_schema_preserves_allowed_roles_and_quotas` (line 9) — schema round-trip.
- `test_mcp_storage_round_trips_tool_policy` (line 26) — `set_tool_policy` → `get_tool_policy` with `allowed_roles` + `role_quotas` for `kunxiaozhi_internal` / `image_generate`.
- **`test_internal_tool_policies_filter_blocked_tools` (line 61-104)** — the closest existing test to the bug. Monkeypatches `internal_registry.MCPStorage` to return a policy `image_generate: MCPToolPolicy(allowed_roles=["admin"])` and `build_internal_tools` to return `[_FakeTool(name="image_generate"), _FakeTool(name="env_var_list")]`. Calls `get_internal_tools_for_user(user_roles=["user"], is_admin=False)` and asserts the result is `["env_var_list"]` (image_generate filtered out because user isn't admin).
  - **Key gap**: This test verifies `get_internal_tools_for_user` in isolation. It does NOT call `context.setup()`, so it does NOT exercise the Path 2 double-load that re-adds filtered tools. The bypass is invisible to this test.
- `test_internal_image_generate_tool_infos_include_supported_parameters` (line 107) — `get_internal_tool_infos` parameter extraction.
- `test_effective_config_loads_system_tool_policies_in_bulk` (line 210) and `test_effective_config_caps_loaded_servers` (line 240) — bulk policy loading for real MCP servers.

### env_var tool behavior — `tests/infra/tool/test_env_var_tool.py`

- `test_get_env_var_tools_returns_safe_crud_tools` (line 96) — verifies the 4 tool names.
- `test_env_var_prompt_*` (lines 111-159) — prompt builder + cache eviction.
- `test_env_var_prompt_middleware_appends_key_list` (line 162) — middleware appends keys to system message.
- `test_env_var_list_returns_masked_values` (line 199), `test_env_var_set_delegates_to_storage_and_masks_response` (line 213), `test_env_var_delete_delegates_to_storage` (line 266), `test_env_var_tool_requires_runtime_user` (line 284) — tool invocation behavior.
- `test_env_var_set_invalidates_prompt_and_syncs_current_sandbox` (line 236) — verifies `_sync_envvar_change` calls `ensure_sandbox_mcp(backend, user_id, force_rebuild=True)`.

### Context setup() env_var loading — `tests/infra/tool/test_env_var_tool.py:302-337`

These two tests are the most relevant to the fix and are a LAND MINE for the fix:

- **`test_search_agent_context_includes_env_var_tools` (line 302-318)**:
  ```python
  monkeypatch.setattr(search_context.settings, "ENABLE_MEMORY", False)
  monkeypatch.setattr(search_context.settings, "ENABLE_SANDBOX", False)
  monkeypatch.setattr(search_context.settings, "ENABLE_SKILLS", False)
  ctx = search_context.SearchAgentContext(user_id="user-1")
  await ctx.setup()
  names = {tool.name for tool in ctx.tools}
  assert {"env_var_list", "env_var_set", "env_var_delete", "env_var_delete_all"} <= names
  ```
  This test explicitly sets `ENABLE_SANDBOX=False` and asserts env_var tools ARE present. It stubs the base tools (`_stub_context_tool_imports`, line 67-93) but does NOT stub `get_internal_tools_for_user` or `get_env_var_tools`, so the test currently passes because Path 2 (or Path 1) loads them.

- **`test_fast_agent_context_includes_env_var_tools` (line 321-337)** — identical structure for FastAgentContext, also `ENABLE_SANDBOX=False` and asserts env_var tools present.

**Implication**: If the fix makes env_var tools sandbox-gated (or removes Path 2 AND gates Path 1 on `ENABLE_SANDBOX`), these two tests will FAIL because they set `ENABLE_SANDBOX=False` and expect env_var tools. They assert the CURRENT (buggy) behavior that env_var tools load regardless of sandbox. The fix must update these tests.

### API routes — `tests/api/test_mcp_routes.py`

- `test_admin_internal_tool_discovery_uses_internal_registry` (line 99-131) — `GET /api/admin/mcp/kunxiaozhi_internal/tools` calls `get_internal_tool_infos` and returns the list. Stubs `get_internal_tool_infos` to return one `image_generate` tool.
- Earlier test (line ~92-96) — admin server list includes `kunxiaozhi_internal`.
- `test_admin_toggle_tool_returns_bad_request_for_disabled_tool_overflow` (line 167) — toggle endpoint overflow for NON-internal servers (uses `set_system_tool_disabled`).

**Gap**: No test exercises `admin_toggle_tool` for an INTERNAL server (`kunxiaozhi_internal`) end-to-end — i.e. toggle a tool off, then call `context.setup()`, and assert the tool is absent. That is exactly the scenario the bug breaks.

### Sandbox gating — `tests/agents/test_team_agent_sandbox_support.py`

- `test_team_agent_node_uses_sandbox_backend_when_enabled` (line 97) — `ENABLE_SANDBOX=True` → sandbox backend used.
- Other tests (line 196, 252) set `ENABLE_SANDBOX=False`.
- **Does NOT test env_var tool loading or EnvVarPromptMiddleware attachment.** Grepping the file for `env_var`/`EnvVarPrompt` returns no matches.

## Gap summary

| Scenario | Tested? |
|---|---|
| `MCPToolPolicy` schema + storage round-trip | YES |
| `get_internal_tools_for_user` filters blocked tools (registry layer, isolated) | YES (`test_internal_tool_policies_filter_blocked_tools`) |
| `get_internal_tool_infos` parameters | YES |
| `env_var_*` tool invocation behavior | YES |
| `env_var_prompt` builder + middleware | YES |
| `_sync_envvar_change` → `ensure_sandbox_mcp` | YES |
| **`context.setup()` respects a disabled `env_var_*` policy (the actual bug)** | **NO** — no test toggles a policy off then runs `setup()` and checks the tool is absent |
| **`context.setup()` does NOT re-add policy-filtered tools via Path 2** | **NO** |
| **`admin_toggle_tool` for `kunxiaozhi_internal` end-to-end → tool absent from agent context** | **NO** |
| **env_var tools absent when `ENABLE_SANDBOX=False` (after fix)** | **NO — current tests assert the OPPOSITE** (`test_search_agent_context_includes_env_var_tools`, `test_fast_agent_context_includes_env_var_tools` expect env_var tools WITH `ENABLE_SANDBOX=False`) |
| `EnvVarPromptMiddleware` not attached when `sandbox_backend is None` | NO direct test |
| `persona_preset_*` / `team_*` / `image_generate` / `audio_transcribe` policy filtering through `context.setup()` | NO (only isolated `get_internal_tools_for_user` test) |

## Tests that will need updating after the fix

1. `tests/infra/tool/test_env_var_tool.py::test_search_agent_context_includes_env_var_tools` (line 302) — asserts env_var tools present with `ENABLE_SANDBOX=False`. If the fix gates env_var tools on `ENABLE_SANDBOX`, this test must either flip `ENABLE_SANDBOX=True` or change its assertion.
2. `tests/infra/tool/test_env_var_tool.py::test_fast_agent_context_includes_env_var_tools` (line 321) — same.

Both tests stub `human_tool`/`reveal_*`/`transfer_*` via `_stub_context_tool_imports` but do NOT stub `get_internal_tools_for_user` or `MCPToolWithRetry`, so they exercise the real internal-registry path (and currently rely on Path 2 to guarantee env_var presence even if Path 1 were to filter them). After removing Path 2, these tests would depend entirely on Path 1 loading env_var tools — which still works today because no policy is configured in the test, so `_is_tool_allowed` returns True for all env_var tools. The tests only break if the fix ALSO adds an `ENABLE_SANDBOX` gate to `build_internal_tools()` for env_var tools.

## Caveats / Not Found

- I did not run the test suite; the above is from reading the test source.
- I did not find a `tests/infra/tool/test_internal_registry.py` file (Glob returned no matches) — the internal_registry tests live in `tests/test_mcp_tool_policies.py` instead.
- There may be integration tests elsewhere that exercise a full agent run with env_var tools; I only grepped the obvious directories. A repo-wide grep for `env_var_set` / `env_var_list` in `tests/` would find any I missed (the grep earlier only covered `tests/` for `get_internal_tools_for_user|build_internal_tools|...`).
