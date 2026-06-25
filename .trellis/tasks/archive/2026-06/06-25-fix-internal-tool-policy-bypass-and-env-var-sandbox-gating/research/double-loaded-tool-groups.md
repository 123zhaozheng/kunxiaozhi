# Research: Which Tool Groups Are Double-Loaded

- **Query**: Cross-reference `build_internal_tools()` (internal_registry.py) against the separate direct-load blocks in both context setup() methods. Identify EVERY tool group that appears in both (env_var, persona_preset, team, image_generation, audio_transcribe, etc.) — these are all potential policy-bypass candidates, not just env_var.
- **Scope**: internal
- **Date**: 2026-06-25

## Method

I compared the tool groups loaded inside `build_internal_tools()` (`src/infra/tool/internal_registry.py:28-41`) against every `get_*_tools()` / `get_*_tool()` call site found by grep across `src/`.

### Calls inside `build_internal_tools()` (internal_registry.py:28-41)

| Line | Call | Tool group | Settings guard inside `build_internal_tools` |
|---|---|---|---|
| 33 | `get_image_generation_tool()` | image_generate | `if settings.ENABLE_IMAGE_GENERATION:` (line 32) |
| 36 | `get_audio_transcribe_tool()` | audio_transcribe | `if settings.ENABLE_AUDIO_TRANSCRIPTION:` (line 35) |
| 38 | `get_env_var_tools()` | env_var_list/set/delete/delete_all | **none** |
| 39 | `get_persona_preset_tools()` | create_persona_preset, update_persona_preset | **none** |
| 40 | `get_team_tools()` | search_persona_presets, create_agent_team | **none** |

### All call sites of these getters across `src/` (grep for `get_env_var_tools()` etc.)

| Getter | Call sites outside `internal_registry.py` |
|---|---|
| `get_env_var_tools()` | `src/agents/fast_agent/context.py:204` (Path 2), `src/agents/search_agent/context.py:221` (Path 2) |
| `get_persona_preset_tools()` | **None** (only internal_registry.py:39) |
| `get_team_tools()` | **None** (only internal_registry.py:40) |
| `get_image_generation_tool()` | **None** (only internal_registry.py:33) |
| `get_audio_transcribe_tool()` | **None** (only internal_registry.py:36) |

## Result: only `env_var_*` is double-loaded

| Tool group | In `build_internal_tools` (Path 1, policy-filtered)? | Also loaded directly in context.setup() (Path 2, dedup-only)? | Policy-bypass risk? |
|---|---|---|---|
| `env_var_*` (4 tools) | YES (internal_registry.py:38) | **YES** — fast_agent/context.py:198-210; search_agent/context.py:215-227 | **YES — confirmed bypass** |
| `persona_preset_*` (2 tools) | YES (internal_registry.py:39) | No | No |
| `team_*` (2 tools) | YES (internal_registry.py:40) | No | No |
| `image_generate` | YES (internal_registry.py:33) | No | No |
| `audio_transcribe` | YES (internal_registry.py:36) | No | No |

## Why only env_var has a direct-load block

The Path 2 blocks in both contexts are specifically scoped to env_var — they import `get_env_var_tools` and nothing else:

`src/agents/fast_agent/context.py:198-210`:
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

`src/agents/search_agent/context.py:215-227` is structurally identical (only the log prefix differs).

There is NO equivalent direct-load `try` block for `get_persona_preset_tools`, `get_team_tools`, `get_image_generation_tool`, or `get_audio_transcribe_tool` in either context (confirmed by grep — those getters are called only from `internal_registry.py`).

## Bypass mechanism (recap, env_var only)

1. Path 1 (`get_internal_tools_for_user`) loads all internal tools, applies `_is_tool_allowed`, and omits any tool whose policy has `disabled=True`. So a disabled `env_var_set` is NOT in `self.tools` after line 193/210.
2. Path 2 then calls `get_env_var_tools()` raw and keeps any tool whose name is not already in `existing_tool_names`. Because the disabled tool was omitted by Path 1, its name is absent from `existing_tool_names`, so the dedup check (line 205 / line 222) keeps it — re-adding the disabled tool as a raw `@tool` with no `MCPToolWithRetry` wrapping and no quota attribution.

## Implication for the fix

- Removing the Path 2 env_var blocks (fast_agent/context.py:198-210, search_agent/context.py:215-227) eliminates the ONLY double-load, and there are no other direct-load blocks to remove. No other tool group needs a similar fix.
- `persona_preset_*`, `team_*`, `image_generate`, `audio_transcribe` already rely solely on Path 1 — toggling them off in the UI works today, and will continue to work after the fix.

## Caveats / Not Found

- The `team_*` tools (`search_persona_presets`, `create_agent_team`) and `persona_preset_*` tools are always built by `build_internal_tools` with no settings guard. If a deployment wants to disable them globally, the only mechanism today is the per-tool MCP policy (UI toggle) — there is no `ENABLE_PERSONA_PRESET` / `ENABLE_TEAM` settings flag.
- I did not find any other file outside `src/agents/**/context.py` and `src/infra/tool/internal_registry.py` that calls these getters, so there are no other double-load sites (e.g. no script or test-fixture production path that would re-add the tools).
