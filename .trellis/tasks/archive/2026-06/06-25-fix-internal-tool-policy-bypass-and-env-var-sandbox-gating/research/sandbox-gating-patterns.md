# Research: ENABLE_SANDBOX Guard Patterns

- **Query**: How is sandbox_mcp_tool / upload_url_tool gated (context.py `if settings.ENABLE_SANDBOX:`)? Is there a settings flag for env var tools? Check src/kernel/config for any relevant flags (ENABLE_ENV_VAR, ENABLE_MCP, etc.).
- **Scope**: internal
- **Date**: 2026-06-25

## Where ENABLE_SANDBOX is checked

Grep across `src/agents/` for `ENABLE_SANDBOX` / `sandbox_backend =`:

| File:line | Code | Purpose |
|---|---|---|
| `src/agents/search_agent/nodes.py:406` | `if not settings.ENABLE_SANDBOX:` → returns a `create_persistent_backend_factory(...)` and `prompt = DEFAULT_SYSTEM_PROMPT`, with `sandbox_backend=None` | Backend selection: no sandbox → PersistentBackend, no sandbox backend |
| `src/agents/search_agent/context.py:243` | `if settings.ENABLE_SANDBOX:` → loads `get_upload_url_tool()` + `get_sandbox_mcp_tools()` | Sandbox-only tools |
| `src/agents/team_agent/nodes.py:265` | `if not settings.ENABLE_SANDBOX:` → `create_persistent_backend_factory(...)`; else resolves sandbox backend | Backend selection |
| `src/agents/fast_agent/context.py:229` | `if settings.ENABLE_SANDBOX:` → `self.tools.extend(get_sandbox_mcp_tools())` | Sandbox-only tools (FastAgent has no `upload_url_tool`) |

`sandbox_backend` is the variable that ends up `None` when `ENABLE_SANDBOX=False`. The middleware attachment sites check `if sandbox_backend:` (not the settings flag directly) — see `research/env-var-consumers.md` Consumer 5.

## How sandbox-only tools are gated in context.setup()

### SearchAgentContext — `src/agents/search_agent/context.py:242-251`

```python
# 沙箱专属工具
if settings.ENABLE_SANDBOX:
    from src.infra.tool.sandbox_mcp_tool import get_sandbox_mcp_tools
    from src.infra.tool.upload_url_tool import get_upload_url_tool

    self.tools.append(get_upload_url_tool())
    logger.info("[SearchAgentContext] Added upload_url_to_sandbox tool (sandbox mode)")

    self.tools.extend(get_sandbox_mcp_tools())
    logger.info("[SearchAgentContext] Added sandbox_mcp tools (sandbox mode)")
```

### FastAgentContext — `src/agents/fast_agent/context.py:228-233`

```python
# 沙箱 MCP 管理工具
if settings.ENABLE_SANDBOX:
    from src.infra.tool.sandbox_mcp_tool import get_sandbox_mcp_tools

    self.tools.extend(get_sandbox_mcp_tools())
    logger.info("[FastAgentContext] Added sandbox_mcp tools (sandbox mode)")
```

(Note: FastAgent does NOT load `upload_url_tool` — only SearchAgent does.)

## The pattern for a sandbox-gated tool group

The established pattern is:

```python
if settings.ENABLE_SANDBOX:
    from src.infra.tool.<x> import get_<x>_tools
    self.tools.extend(get_<x>_tools())
```

The env_var direct-load block (Path 2) in both contexts does NOT follow this pattern — it has no `if settings.ENABLE_SANDBOX:` guard. Neither does the Path 1 internal-tools block (`get_internal_tools_for_user`).

## Is there a settings flag for env var tools?

**No.** Grep across `src/kernel/config/` and all of `src/`:

- `ENABLE_ENV_VAR` → No matches
- `ENV_VAR_ENABLED` → No matches
- `ENABLE_INTERNAL_TOOLS` → No matches

The env var tools have NO dedicated settings flag. They are always built by `build_internal_tools()` (internal_registry.py:38, no guard) and always re-added by Path 2 (no guard).

## Relevant flags that DO exist — `src/kernel/config/base.py`

| Flag | Default | File:line | Used to gate |
|---|---|---|---|
| `ENABLE_MCP` | `True` | base.py:85 | Lazy MCP tool loading (context.py:106 / 98) |
| `ENABLE_DEFERRED_TOOL_LOADING` | `True` | base.py:86 | Deferred MCP tool loading |
| `ENABLE_SANDBOX` | `True` | base.py:178 | Sandbox backend + sandbox-only tools + EnvVarPromptMiddleware attachment |
| `ENABLE_SKILLS` | `True` | base.py:198 | Skills loading |
| `ENABLE_MEMORY` | `False` | base.py:285 | Memory tools |
| `ENABLE_AUDIO_TRANSCRIPTION` | `False` | base.py:315 | `audio_transcribe` tool inside `build_internal_tools` (internal_registry.py:35) |
| `ENABLE_IMAGE_GENERATION` | `False` | base.py:320 | `image_generate` tool inside `build_internal_tools` (internal_registry.py:32) |

So `image_generate` and `audio_transcribe` ARE settings-gated inside `build_internal_tools()`, but `env_var_*`, `persona_preset_*`, and `team_*` are NOT. The only way to disable `env_var_*` today is the per-tool MCP policy (UI toggle), which Path 2 bypasses.

## Pattern comparison (what the fix could mirror)

| Tool group | How it's gated today |
|---|---|
| `sandbox_mcp_*`, `upload_url_tool` | `if settings.ENABLE_SANDBOX:` in context.setup() — NOT in internal_registry |
| `image_generate` | `if settings.ENABLE_IMAGE_GENERATION:` inside `build_internal_tools()` |
| `audio_transcribe` | `if settings.ENABLE_AUDIO_TRANSCRIPTION:` inside `build_internal_tools()` |
| `env_var_*` | **No guard anywhere** — always built (Path 1) and always re-added (Path 2) |
| `persona_preset_*`, `team_*` | **No guard anywhere** — always built (Path 1 only) |

If the fix wants env_var tools to be sandbox-gated (matching the architecture concern that they have no non-sandbox consumer — see `research/env-var-consumers.md`), the natural place is `build_internal_tools()` (internal_registry.py:38), wrapping the `tools.extend(get_env_var_tools())` line in `if settings.ENABLE_SANDBOX:`. This mirrors the `image_generate`/`audio_transcribe` pattern exactly. Combined with removing Path 2, this would:
- load env_var tools only when sandbox is on (single source of truth),
- apply policy filtering (Path 1 only),
- and make the MCP UI toggle effective.

## Caveats / Not Found

- I did not check whether the frontend or any API endpoint assumes `env_var_*` tools always exist (e.g. a help doc or tool list). If env_var tools become sandbox-gated, any non-sandbox deployment would no longer expose them. A grep for `env_var_list` / `env_var_set` across `frontend/` and `src/api/` would confirm whether anything depends on their presence. (The `kunxiaozhi_internal` tool-discovery endpoint `get_internal_tool_infos` would simply stop listing them when sandbox is off, because `build_internal_tools()` would omit them.)
- `ENABLE_CODE_INTERPRETER` (base.py:201, default False) exists but is unrelated to env vars.
