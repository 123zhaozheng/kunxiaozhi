# Research: Impact Analysis of Removing Path 2

- **Query**: If we delete the redundant direct-load blocks (Path 2) and rely solely on get_internal_tools_for_user, what breaks? Check: are there tools loaded in Path 2 that are NOT in build_internal_tools()? Are there agents/contexts that don't call get_internal_tools_for_user at all?
- **Scope**: internal
- **Date**: 2026-06-25

## What "Path 2" is

The redundant direct-load `try` blocks in both context `setup()` methods:

- `src/agents/fast_agent/context.py:198-210` — loads `get_env_var_tools()` raw, name-deduped against `self.tools`.
- `src/agents/search_agent/context.py:215-227` — same.

`TeamAgentContext` inherits `FastAgentContext.setup()`, so it shares the fast_agent Path 2 block.

## Are there tools loaded in Path 2 that are NOT in `build_internal_tools()`?

**No.** Path 2 loads ONLY `get_env_var_tools()`, and `build_internal_tools()` (internal_registry.py:38) already does `tools.extend(get_env_var_tools())`. The exact same function, the exact same four tools (`env_var_list`, `env_var_set`, `env_var_delete`, `env_var_delete_all`).

So removing Path 2 does NOT drop any tool that Path 1 wouldn't already provide (assuming Path 1's policy allows it and `ENABLE_SANDBOX`/image/audio guards don't apply — env_var has no guard in `build_internal_tools` today).

| Tool in Path 2 | Also in `build_internal_tools()`? | After removing Path 2, still loaded? |
|---|---|---|
| `env_var_list` | YES (internal_registry.py:38) | YES — via Path 1 (unless policy disables it, which is the desired behavior) |
| `env_var_set` | YES | YES (same) |
| `env_var_delete` | YES | YES (same) |
| `env_var_delete_all` | YES | YES (same) |

## Are there agents/contexts that don't call `get_internal_tools_for_user` at all?

**No.** All three contexts call it in `setup()`:

- `FastAgentContext.setup()` — fast_agent/context.py:188-193:
  ```python
  internal_tools = await get_internal_tools_for_user(
      user_id=self.user_id, user_roles=user_roles, is_admin=is_admin,
  )
  self.tools.extend(internal_tools)
  ```
- `SearchAgentContext.setup()` — search_agent/context.py:205-210 (identical call).
- `TeamAgentContext.setup()` — inherited from FastAgentContext, so the same call runs.

The call is wrapped in `try/except` in both contexts (fast_agent/context.py:182-196, search_agent/context.py:199-213). If `get_internal_tools_for_user` raises, the except logs a warning and Path 2 currently re-adds env_var tools as a fallback. After removing Path 2, a Path 1 failure would mean NO env_var tools loaded for that session — but also no persona/team/image/audio tools, which is the existing behavior for those groups (they have no fallback). So env_var would simply become consistent with the other internal tool groups.

## What breaks if Path 2 is removed?

### Behavior changes (intended)

1. **Policy bypass is fixed.** A disabled `env_var_*` tool (via the MCP UI toggle, which writes `MCPToolPolicy.disabled=True`) is now actually absent from `self.tools`. Toggling env_var tools off works.
2. **env_var tools get `MCPToolWithRetry` wrapping + quota attribution.** Today Path 2 inserts raw `@tool` objects that skip retry and per-role quotas. After removal, env_var tools go through the same wrapping as persona/team/image tools.
3. **Role-based access control applies to env_var tools.** If an admin sets `allowed_roles=["admin"]` on an env_var tool, non-admin users will no longer receive it. Today Path 2 re-adds it regardless of role.

### Behavior changes (side effects to verify)

4. **Path 1 failure mode changes.** If `get_internal_tools_for_user` raises (e.g. Mongo unavailable when reading `list_tool_policies`), env_var tools no longer load via fallback. Note: `get_internal_tool_policies` (internal_registry.py:147-152) already swallows exceptions and returns `{}`, so `get_internal_tools_for_user` only raises if `build_internal_tools()` itself raises (unlikely — it just calls the getters) or if `MCPToolWithRetry` construction raises. Risk is low.
5. **Tests `test_search_agent_context_includes_env_var_tools` and `test_fast_agent_context_includes_env_var_tools`** (tests/infra/tool/test_env_var_tool.py:302, 321). These set `ENABLE_SANDBOX=False` and assert env_var tools are present. After removing Path 2:
   - If the fix is **ONLY removing Path 2** (no sandbox gate added to `build_internal_tools`): these tests still PASS, because Path 1 still loads env_var tools (no policy configured in the test → all allowed, and env_var has no `ENABLE_SANDBOX` guard in `build_internal_tools` today). The tests would then be exercising Path 1 instead of Path 2.
   - If the fix **ALSO gates env_var on `ENABLE_SANDBOX`** inside `build_internal_tools`: these tests FAIL (they set `ENABLE_SANDBOX=False` and expect env_var tools). They must be updated to either set `ENABLE_SANDBOX=True` or assert env_var tools are absent.

### What does NOT break

- `persona_preset_*`, `team_*`, `image_generate`, `audio_transcribe` — unaffected; they were never in Path 2.
- `sandbox_mcp_*`, `upload_url_tool` — unaffected; loaded in separate `if settings.ENABLE_SANDBOX:` blocks, not in Path 2.
- Base tools (`human_tool`, `reveal_*`, `transfer_*`) — unaffected; loaded before Path 1/2.
- Memory tools, skills, lazy MCP tools — unaffected.
- The `kunxiaozhi_internal` virtual server UI listing (`build_internal_server_response`) — unaffected.
- The admin tool-discovery endpoint (`get_internal_tool_infos`) — unaffected; it already reads from `build_internal_tools()` + policies, not from any context's `self.tools`.

## Are there other consumers of the raw `get_env_var_tools()`?

Grep for `get_env_var_tools()` across `src/` returns only:
- `src/infra/tool/env_var_tool.py:170` (the definition)
- `src/infra/tool/internal_registry.py:38` (Path 1)
- `src/agents/fast_agent/context.py:204` (Path 2)
- `src/agents/search_agent/context.py:221` (Path 2)

No other code calls `get_env_var_tools()` directly. The tests (`tests/infra/tool/test_env_var_tool.py:97`) call it to assert shape, but that's a test of the getter itself, not a production consumer.

## Recommended fix shape (for the PRD, not implementing here)

Two independent changes, each safe on its own:

**Change A — Remove Path 2 (fixes the bypass):**
Delete fast_agent/context.py:198-210 and search_agent/context.py:215-227. After this, env_var tools load only via `get_internal_tools_for_user` (policy-filtered, wrapped). This alone fixes the policy bypass and makes env_var consistent with persona/team/image/audio.

**Change B — Gate env_var tools on `ENABLE_SANDBOX` (fixes the dead-weight architecture concern):**
In `build_internal_tools()` (internal_registry.py:38), wrap `tools.extend(get_env_var_tools())` in `if settings.ENABLE_SANDBOX:`. This mirrors the `image_generate`/`audio_transcribe` pattern. With this, non-sandbox deployments no longer load env_var tools at all (no consumer exists — see `research/env-var-consumers.md`). Requires updating the two context tests noted above.

Change B depends on Change A (without A, Path 2 would still re-add env_var tools even when `build_internal_tools` omits them — the dedup check would pass because the names wouldn't be in `existing_tool_names`). So both must ship together for the sandbox gate to be effective.

## Caveats / Not Found

- I did not verify whether `MCPToolWithRetry` wrapping changes the tool's `args_schema` or `coroutine` attribute in a way that breaks the existing `env_var_*` tool tests (`test_env_var_*` tests call `env_var_tool.env_var_set.coroutine(...)` directly on the raw `@tool`, not on a wrapped instance — so they're unaffected by wrapping). The context-level tests (`test_*_context_includes_env_var_tools`) only check `tool.name`, which `MCPToolWithRetry` preserves (mcp_client.py:92 `name=original_tool.name`).
- I did not check whether any frontend code or API response expects `env_var_*` tools to always be present in the agent's tool list (e.g. a UI help panel). If Change B is adopted, a non-sandbox deployment's `get_internal_tool_infos` would no longer list env_var tools, which is the correct behavior but could surprise a UI that hardcodes their existence.
- The `try/except` around the Path 1 block means a Path 1 failure today is silent (logged as warning) and Path 2 masks it for env_var. After removing Path 2, operators should watch for `[FastAgentContext] Failed to load internal tools: ...` warnings as the signal that env_var (and other internal) tools failed to load.
