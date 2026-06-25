# Research: All Tool-Loading Sites in Agent Contexts

- **Query**: Find every place tools are added to `self.tools` across FastAgentContext, SearchAgentContext, TeamAgentContext (and any other context). For each: which tool groups, what guards (if any), and whether they go through policy-filtering.
- **Scope**: internal
- **Date**: 2026-06-25

## Agent Contexts That Exist

Only three context classes exist in the codebase (Glob `src/agents/**/context.py`):

| Context | File | Notes |
|---|---|---|
| `FastAgentContext` | `src/agents/fast_agent/context.py` | No-sandbox context, supports tools + skills |
| `SearchAgentContext` | `src/agents/search_agent/context.py` | Supports tools + skills |
| `TeamAgentContext` | `src/agents/team_agent/context.py` | **Inherits from `FastAgentContext`** (line 6: `class TeamAgentContext(FastAgentContext)`); body is `pass` (line 9). It reuses `FastAgentContext.setup()` unchanged. |

`TeamAgentContext` adds NO tool-loading code of its own — its `setup()` is inherited verbatim from `FastAgentContext`. So the FastAgent table below also describes TeamAgent.

## FastAgentContext.setup() — `src/agents/fast_agent/context.py:155-267`

Order of `self.tools.append` / `self.tools.extend` calls inside `setup()`:

| # | Line | Tool group / tool | Guard | Goes through `get_internal_tools_for_user` (policy + MCPToolWithRetry)? |
|---|---|---|---|---|
| 1 | 162-164 | `human_tool` (`get_human_tool`) | None | No (base tool, not an internal MCP tool) |
| 2 | 166-168 | `reveal_file_tool` (`get_reveal_file_tool`) | None | No (base tool) |
| 3 | 170-172 | `reveal_project_tool` | None | No (base tool) |
| 4 | 174-176 | `transfer_file_tool` | None | No (base tool) |
| 5 | 178-180 | `transfer_path_tool` | None | No (base tool) |
| 6 | 182-196 | **Internal tools** via `get_internal_tools_for_user(user_id, user_roles, is_admin)` — includes env_var_*, persona_preset_*, team_*, image_generate, audio_transcribe | Wrapped in `try/except` (lines 182-196). `user_roles, is_admin` resolved via `resolve_user_mcp_access(self.user_id)` (lines 185-187). **No ENABLE_SANDBOX guard.** | **YES** — this is the policy-filtered path (Path 1) |
| 7 | 198-210 | **`env_var_tools` via direct `get_env_var_tools()` call** — deduped by name against `existing_tool_names` | Wrapped in `try/except` (lines 198-210). **No ENABLE_SANDBOX guard. No policy check.** Dedup only: `if getattr(tool, "name", "") not in existing_tool_names` (line 205) | **NO** — raw `@tool` objects, no `MCPToolWithRetry` wrapping, no policy filter (Path 2) |
| 8 | 212-223 | memory tools (`get_all_memory_tools`) | `if settings.ENABLE_MEMORY:` (line 213) | No (separate memory subsystem) |
| 9 | 228-233 | `sandbox_mcp_tools` (`get_sandbox_mcp_tools`) | `if settings.ENABLE_SANDBOX:` (line 229) | No (sandbox management tools, not internal-registry tools) |
| - | 236-265 | skills loading | `if settings.ENABLE_SKILLS and self.user_id:` | N/A (skills, not tools) |

Plus lazy-loaded MCP tools in `_lazy_load_mcp_tools` (lines 99-153), appended on first `get_tools()` call, gated by `settings.ENABLE_MCP` (line 106).

### The bypass mechanism (FastAgent, lines 198-210)

```python
try:
    from src.infra.tool.env_var_tool import get_env_var_tools

    existing_tool_names = {getattr(tool, "name", "") for tool in self.tools}
    env_var_tools = [
        tool
        for tool in get_env_var_tools()
        if getattr(tool, "name", "") not in existing_tool_names
    ]
    self.tools.extend(env_var_tools)
    logger.info(f"[FastAgentContext] Added {len(env_var_tools)} env var tools")
except Exception as e:
    logger.warning(f"[FastAgentContext] Failed to load env var tools: {e}")
```

When Path 1 (line 188) filters out a disabled `env_var_*` tool, that tool's name is NOT in `existing_tool_names`, so the dedup check on line 205 is `True` and the disabled tool is re-added on line 207 — bypassing the policy. Toggling `env_var_*` off in the MCP UI therefore has no effect.

## SearchAgentContext.setup() — `src/agents/search_agent/context.py:172-281`

| # | Line | Tool group / tool | Guard | Policy-filtered? |
|---|---|---|---|---|
| 1 | 179-181 | `human_tool` | None | No (base tool) |
| 2 | 183-185 | `reveal_file_tool` | None | No |
| 3 | 187-189 | `reveal_project_tool` | None | No |
| 4 | 191-193 | `transfer_file_tool` | None | No |
| 5 | 195-197 | `transfer_path_tool` | None | No |
| 6 | 199-213 | **Internal tools** via `get_internal_tools_for_user(...)` | `try/except`; `user_roles, is_admin` via `resolve_user_mcp_access`; **no ENABLE_SANDBOX guard** | **YES (Path 1)** |
| 7 | 215-227 | **`env_var_tools` via direct `get_env_var_tools()`** — name-deduped | `try/except`; **no guard, no policy** | **NO (Path 2)** — same bypass as FastAgent |
| 8 | 229-240 | memory tools | `if settings.ENABLE_MEMORY:` (line 230) | No |
| 9 | 243-248 | `upload_url_tool` (`get_upload_url_tool`) | `if settings.ENABLE_SANDBOX:` (line 243) | No (sandbox-only tool) |
| 10 | 250-251 | `sandbox_mcp_tools` | `if settings.ENABLE_SANDBOX:` (line 243, same block) | No |
| - | 257-279 | skills | `if settings.ENABLE_SKILLS:` | N/A |

Plus lazy MCP tools (lines 91-151), gated by `ENABLE_MCP`.

### The bypass mechanism (SearchAgent, lines 215-227)

```python
try:
    from src.infra.tool.env_var_tool import get_env_var_tools

    existing_tool_names = {getattr(tool, "name", "") for tool in self.tools}
    env_var_tools = [
        tool
        for tool in get_env_var_tools()
        if getattr(tool, "name", "") not in existing_tool_names
    ]
    self.tools.extend(env_var_tools)
    logger.info(f"[SearchAgentContext] Added {len(env_var_tools)} env var tools")
except Exception as e:
    logger.warning(f"[SearchAgentContext] Failed to load env var tools: {e}")
```

Identical structure to FastAgent's Path 2 — same dedup-only "filter", same bypass.

## TeamAgentContext.setup()

Inherited from `FastAgentContext` (`src/agents/team_agent/context.py:6-9`). The FastAgent table above applies in full. TeamAgent therefore also has the env_var double-load bypass.

## Other tool-loading sites (NOT in contexts)

These are not `self.tools` mutations but are the source functions the contexts call:

- `src/infra/tool/internal_registry.py:28-41` — `build_internal_tools()` aggregates image_generate (gated `ENABLE_IMAGE_GENERATION`), audio_transcribe (gated `ENABLE_AUDIO_TRANSCRIPTION`), env_var, persona_preset, team tools.
- `src/infra/tool/internal_registry.py:155-184` — `get_internal_tools_for_user()` applies policy + wraps in `MCPToolWithRetry`.
- Lazy MCP loading in `_lazy_load_mcp_tools` (both contexts) calls `get_global_mcp_tools(self.user_id)` then `filter_mcp_tools_by_db_state` — separate DB-disabled filter, not the policy path.

## Key takeaway

Only **`env_var_*`** is double-loaded (Path 1 + Path 2). `persona_preset_*`, `team_*`, `image_generate`, `audio_transcribe` are loaded ONLY through `get_internal_tools_for_user` (Path 1) — they have no direct-load block in either context, so toggling them off in the UI works correctly. See `research/double-loaded-tool-groups.md`.

## Caveats / Not Found

- No other agent context classes exist beyond the three above.
- The `try/except` around Path 1 (lines 182-196 / 199-213) means if `get_internal_tools_for_user` throws, NO internal tools are loaded, but Path 2 still runs and loads env_var tools raw — a partial-failure edge case worth noting for the fix.
