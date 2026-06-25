# Research: env_var Consumers and the Sandbox Coupling

- **Query**: Enumerate every consumer of stored env vars: rebuild_sandbox_mcp, _sync_user_env_vars, build_env_flags, EnvVarPromptMiddleware, sandbox_mcp_prompt. For each: is it gated on sandbox? What happens without sandbox? Confirm whether env vars have ANY non-sandbox consumer.
- **Scope**: internal
- **Date**: 2026-06-25

## Where stored env vars live

- Storage: `src/infra/envvar/storage.py` — `EnvVarStorage` with `set_var` / `list_vars` / `delete_var` / `delete_all_vars` / `get_decrypted_vars`. Encrypted at rest; `list_vars` returns masked `***` values; `get_decrypted_vars` returns the plaintext `dict[str,str]`.
- Written by: the `env_var_*` LLM tools (`src/infra/tool/env_var_tool.py`) and (for the encrypted values) read by the sandbox rebuild/sync paths below.

## Consumer 1: `rebuild_sandbox_mcp` — `src/infra/tool/sandbox_mcp_rebuild.py:158-272`

- **Reads env vars?** YES — `env_storage.get_decrypted_vars(user_id)` at line 233 (`env_vars = await env_storage.get_decrypted_vars(user_id)`).
- **Gated on sandbox?** YES, implicitly — the function takes a `backend: Any` (sandbox backend) and runs `mcporter` inside it (`backend.aexecute("mcporter --version", ...)` line 179). With no sandbox backend there is no caller and no execution. Also uses `build_env_flags(user_id, env_keys)` at line 244 to inject `--env KEY=VALUE` into each `mcporter config add` command.
- **Without sandbox?** Never called — `rebuild_sandbox_mcp` is only invoked via `ensure_sandbox_mcp` (line 300), which is only called from sandbox session_manager paths and from the env_var tools' `_sync_envvar_change` (only when `backend is not None`).

## Consumer 2: `ensure_sandbox_mcp` — `src/infra/tool/sandbox_mcp_rebuild.py:275-320`

- **Reads env vars?** Indirectly — calls `rebuild_sandbox_mcp` and `_sync_user_env_vars`.
- **Gated on sandbox?** YES — requires a `backend` argument; the function is only meaningfully called when a sandbox backend exists.
- **Without sandbox?** Not called. Note the guard inside env_var_tool's `_sync_envvar_change` (env_var_tool.py:49): `if backend is not None: await ensure_sandbox_mcp(backend, user_id, force_rebuild=True)`. When sandbox is off, `get_backend_from_runtime(runtime)` may still return a `PersistentBackend`, in which case `ensure_sandbox_mcp` would be called with a non-sandbox backend — see "Edge case" below.

## Consumer 3: `_sync_user_env_vars` — `src/infra/tool/sandbox_mcp_rebuild.py:323-349`

- **Reads env vars?** YES — `env_storage.get_decrypted_vars(user_id)` at line 338, then sets `sandbox_backend.env_vars = env_vars or {}` at line 346 so the sandbox SDK passes them to every `execute()` call.
- **Gated on sandbox?** YES — `sandbox_backend = getattr(backend, "default", backend)` (line 344); only sets `env_vars` if `hasattr(sandbox_backend, "env_vars")` (line 345). A non-sandbox backend typically has no `env_vars` attribute, so the sync silently does nothing.
- **Without sandbox?** Called by `ensure_sandbox_mcp` (line 319) unconditionally after the rebuild block. If handed a `PersistentBackend`, the `hasattr` check fails and env vars are NOT set anywhere — they are read from storage and dropped.

## Consumer 4: `build_env_flags` — `src/infra/tool/sandbox_mcp_utils.py:11-33`

- **Reads env vars?** YES — `EnvVarStorage().get_decrypted_vars(user_id)` at line 28, builds ` --env KEY=VALUE` flags for `mcporter config add` commands.
- **Gated on sandbox?** YES — only called from `rebuild_sandbox_mcp` (sandbox_mcp_rebuild.py:244) and `sandbox_mcp_tool.py` (lines 146, 218, 223) which are all sandbox-mcporter operations.
- **Without sandbox?** Not called.

## Consumer 5: `EnvVarPromptMiddleware` — `src/infra/agent/middleware/prompt_injection.py:139-160`

- **Reads env vars?** YES — `build_env_var_prompt_sections(self._user_id)` at line 156, which lists env var KEYS (masked) and injects them into the system prompt so the LLM knows which `$KEY` names it can use in shell code.
- **Gated on sandbox?** YES, at the **attachment site**, not in the middleware itself:
  - `src/agents/search_agent/nodes.py:199-200` — `if sandbox_backend: subagent_middleware.append(EnvVarPromptMiddleware(...))`
  - `src/agents/search_agent/nodes.py:249-253` — `if sandbox_backend: user_middleware.append(SandboxMCPMiddleware(...)); user_middleware.append(EnvVarPromptMiddleware(...))`
  - `src/agents/team_agent/nodes.py:360-361` — `if sandbox_backend: mw.append(EnvVarPromptMiddleware(...))`
  - `src/agents/team_agent/nodes.py:491-495` — `if sandbox_backend: user_middleware.append(SandboxMCPMiddleware(...)); user_middleware.append(EnvVarPromptMiddleware(...))`
  - `src/agents/fast_agent/nodes.py` — **NOT attached at all** (grep for `EnvVarPromptMiddleware` in fast_agent/nodes.py: No matches).
- **Without sandbox?** The middleware is never appended, so the LLM is never told which env var keys exist. Even though `build_env_var_prompt_sections` would happily list keys, nobody calls it.

## Consumer 6: `build_env_var_prompt_sections` / `build_env_var_prompt` — `src/infra/tool/env_var_prompt.py:19-61`

- **Reads env vars?** YES — `EnvVarStorage().list_vars(user_id)` at line 33 (keys only, masked).
- **Gated on sandbox?** NOT in the function itself — it's a pure prompt builder. But its only caller is `EnvVarPromptMiddleware` (prompt_injection.py:154-156), which is sandbox-gated at its attachment sites (see Consumer 5). So effectively sandbox-gated.
- **Without sandbox?** Not invoked (no middleware attached).

## Consumer 7: `sandbox_mcp_prompt` / `SandboxMCPMiddleware` — `src/infra/tool/sandbox_mcp_prompt.py` + `prompt_injection.py:107-136`

- **Reads env vars?** NO — this builds the mcporter tool list prompt, not env vars. Mentioned for completeness because it's always attached alongside `EnvVarPromptMiddleware`.
- **Gated on sandbox?** YES — same `if sandbox_backend:` blocks as Consumer 5.

## Consumer 8 (callers, indirect): `_sync_envvar_change` — `src/infra/tool/env_var_tool.py:46-50`

```python
async def _sync_envvar_change(user_id: str, backend: Any | None) -> None:
    invalidate_env_var_prompt_cache(user_id)
    await publish_tool_cache_invalidation("env_var_prompt", user_id=user_id)
    if backend is not None:
        await ensure_sandbox_mcp(backend, user_id, force_rebuild=True)
```

Called from `env_var_set`, `env_var_delete`, `env_var_delete_all` (env_var_tool.py:106, 139, 158). The cache invalidation always runs; the sandbox rebuild only runs when a backend is present.

There is also a parallel helper `src/infra/envvar/sync.py:18-30` (`sync_envvar_change`) that does the same thing and resolves the backend from `get_session_sandbox_manager().get_cached_backend(user_id)` when not passed.

## Edge case: `ENABLE_SANDBOX=False` but `env_var_*` tools still loaded and callable

When `ENABLE_SANDBOX=False`:

1. `env_var_*` tools are still loaded into the agent (Path 1 + Path 2 in both contexts — neither is sandbox-gated; see `research/tool-loading-sites.md` and `research/sandbox-gating-patterns.md`).
2. The LLM can call `env_var_set` / `env_var_delete` / `env_var_delete_all` / `env_var_list`. `set`/`delete`/`delete_all` successfully write/remove rows in the encrypted EnvVarStorage.
3. `get_backend_from_runtime(runtime)` (backend_utils.py:62) returns the `PersistentBackend` factory's result (the non-sandbox backend), which is `not None`, so `_sync_envvar_change` calls `ensure_sandbox_mcp(PersistentBackend, ...)`.
4. Inside `ensure_sandbox_mcp` → `rebuild_sandbox_mcp`: `backend.aexecute("mcporter --version", ...)` on a `PersistentBackend` — behavior depends on whether `PersistentBackend` has `aexecute`; if it doesn't, the call raises and the rebuild is a no-op (caught). `_sync_user_env_vars` then runs but `hasattr(sandbox_backend, "env_vars")` is False for a typical persistent backend, so env vars are dropped.
5. `EnvVarPromptMiddleware` is NOT attached (sandbox-gated), so the LLM is never told the stored keys exist.
6. No sandbox MCP server registration happens, so the stored values are never injected into any command execution.

**Net effect without sandbox**: `env_var_set`/`delete`/`delete_all` mutate storage but the stored values have NO consumer — they're dead weight. `env_var_list` returns masked keys but the LLM has no prompt section telling it to use them, and there's no sandbox to inject them into. The tools are functionally useless without sandbox, yet they consume tool-context slots and (worse, via Path 2) bypass the MCP UI disable policy.

## Conclusion: do env vars have ANY non-sandbox consumer?

**No.** Every consumer is either:
- a sandbox-mcporter operation (`rebuild_sandbox_mcp`, `build_env_flags`, `sandbox_mcp_tool`), or
- a sandbox-backend env injection (`_sync_user_env_vars`), or
- the `EnvVarPromptMiddleware`, which is only attached inside `if sandbox_backend:` blocks (and not at all in fast_agent/nodes.py).

`build_env_var_prompt_sections` is a pure function with no sandbox dependency, but its only caller (`EnvVarPromptMiddleware`) is sandbox-gated. There is no code path that reads stored env vars for any purpose other than sandbox command execution or the sandbox env-var prompt section.

## Caveats / Not Found

- I did not exhaustively read `PersistentBackend` to confirm whether it has an `env_vars` attribute or `aexecute` method. The `hasattr(sandbox_backend, "env_vars")` check (sandbox_mcp_rebuild.py:345) is the documented guard; whether `PersistentBackend` accidentally has an `env_vars` attribute would determine if env vars get silently set on a non-sandbox backend. Worth a follow-up read of the persistent backend class if the fix decides to keep env_var tools available without sandbox.
- The `envvar/sync.py` `sync_envvar_change` helper is a near-duplicate of `env_var_tool._sync_envvar_change`; only the latter is wired to the LLM tools. `sync_envvar_change` appears to be used by other callers (e.g. API routes that set env vars on behalf of a user) — not read in full.
