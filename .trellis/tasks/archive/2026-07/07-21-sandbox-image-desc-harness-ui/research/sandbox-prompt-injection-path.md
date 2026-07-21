# Research: Sandbox system-prompt injection path

- **Query**: Map the full path of sandbox-related system prompt injection after sandbox attach; recommend where to inject image/template capability description.
- **Scope**: internal
- **Date**: 2026-07-21

## Summary

Sandbox prompt material today is split into four layers:

| Layer | What | Where attached | Main agent | Subagent |
|---|---|---|---|---|
| Base system prompt | `SANDBOX_SYSTEM_PROMPT` (storage architecture) | `create_deep_agent(system_prompt=...)` | search + team (sandbox on) | N/A (subagent uses `SUBAGENT_PROMPT`) |
| Runtime section | `SANDBOX_RUNTIME_SECTION` (`work_dir`) | `SectionPromptMiddleware(sections=...)` | yes | yes |
| Sandbox MCP list | mcporter tool inventory | `SandboxMCPMiddleware` | yes only | **no** |
| Env var keys | key names only | `EnvVarPromptMiddleware` | yes | yes |

There is **no** existing image/template/capability-description field or injection.

Fast agent never creates a sandbox backend and never injects any of the four layers above.

`SectionPromptMiddleware` already drops empty/whitespace sections at construct time — empty admin text can naturally no-op without special branching.

---

## Injection pipeline (by agent)

### Middleware primitives

File: `src/infra/agent/middleware/prompt_injection.py`

- **`SectionPromptMiddleware`**: takes `sections: list[str]`; normalizes; **skips empty** (`if section.strip()`); appends each as a separate system text block (KV-cache friendly).
- **`SandboxMCPMiddleware`**: request-time async; calls `build_sandbox_mcp_prompt_sections(backend, user_id)`; appends at tail. Main-agent only in current wiring.
- **`EnvVarPromptMiddleware`**: request-time async; `build_env_var_prompt_sections(user_id)`; key names only.
- **`MemoryIndexMiddleware`**: unrelated to sandbox; dynamic per-user memory index.

Export surface: `src/infra/agent/middleware/__init__.py`.

### search_agent

Files:

- prompts: `src/agents/search_agent/prompt.py`
- wiring: `src/agents/search_agent/nodes.py`

#### Base system prompt

`_create_backend_and_prompt` (`nodes.py` ~390–466):

- `ENABLE_SANDBOX=False` → `DEFAULT_SYSTEM_PROMPT`, `sandbox_backend=None`
- `ENABLE_SANDBOX=True` → `sandbox_manager.get_or_create(...)` → returns  
  `(create_sandbox_backend_factory(...), SANDBOX_SYSTEM_PROMPT, store, sandbox_backend, work_dir)`

`SANDBOX_SYSTEM_PROMPT` is harness-selected (legacy / compact_en / compact_zh) storage architecture text. It has **no** `{work_dir}` placeholder (kept stable for KV cache).

#### Main agent middleware order (comment in nodes: stable → semi-stable → dynamic → cache breakpoint)

1. `create_retry_middleware(...)`
2. `MCPQuotaMiddleware`
3. `ToolResultBinaryMiddleware`
4. **`SectionPromptMiddleware(sections=_prompt_sections)`** once (duplicate class instances rejected by langchain factory)
   - `MAIN_AGENT_PROMPT_SECTIONS` (file/workspace/reveal/safety/tool-discovery)
   - persona sections
   - skills prompt
   - memory guide
   - **if `sandbox_backend` and `sandbox_work_dir`**: `SANDBOX_RUNTIME_SECTION.format(work_dir=...)`
   - optional goal section
5. **if `sandbox_backend`**: `SandboxMCPMiddleware(backend=..., user_id=...)`
6. **if `sandbox_backend`**: `EnvVarPromptMiddleware(user_id=...)`
7. optional `MemoryIndexMiddleware`
8. optional `ToolSearchMiddleware`
9. `create_code_interpreter_middleware(...)`
10. optional goal rubric
11. `PromptCachingMiddleware()`

#### Subagent middleware

- `SectionPromptMiddleware` sections = persona + skills + memory + **runtime section if sandbox**
- **if `sandbox_backend`**: `EnvVarPromptMiddleware` only
- **no** `SandboxMCPMiddleware` on subagents
- ends with `PromptCachingMiddleware`

Gate condition everywhere is **`if sandbox_backend:`** (instance), not re-reading `settings.ENABLE_SANDBOX` at middleware attach time.

### team_agent

Files:

- local prompt defs (partially unused for injection): `src/agents/team_agent/prompt.py`
- wiring: `src/agents/team_agent/nodes.py`

**Important:** `nodes.py` imports runtime/system sandbox strings **from search_agent**:

```python
from src.agents.search_agent.prompt import (
    SANDBOX_RUNTIME_SECTION as SEARCH_SANDBOX_RUNTIME_SECTION,
    SANDBOX_SYSTEM_PROMPT as SEARCH_SANDBOX_SYSTEM_PROMPT,
)
```

`team_agent/prompt.py` also defines `SANDBOX_SYSTEM_PROMPT` / `SANDBOX_RUNTIME_SECTION`, but the live injection path uses the **search_agent** copies. Team-local copies are effectively parallel/dead for nodes injection.

#### Base system prompt when sandbox on

- Team selected: `system_prompt = f"{SEARCH_SANDBOX_SYSTEM_PROMPT}\n\n{router_prompt}"`
- No team: `build_no_team_fallback_system_prompt(sandbox_active=True)` → `SEARCH_SANDBOX_SYSTEM_PROMPT`
- Sandbox off + no team: `FAST_SYSTEM_PROMPT`

#### Main middleware (sandbox-related)

Same pattern as search:

- `_prompt_sections` includes `SEARCH_SANDBOX_RUNTIME_SECTION` when `sandbox_backend and sandbox_work_dir`
- then `SandboxMCPMiddleware` + `EnvVarPromptMiddleware` if `sandbox_backend`

#### Subagent middleware

- Role / general-purpose subagents get `subagent_runtime_section` inside `prompt_sections` → `SectionPromptMiddleware`
- `EnvVarPromptMiddleware` if sandbox
- **no** `SandboxMCPMiddleware`

### fast_agent

Files: `src/agents/fast_agent/prompt.py`, `src/agents/fast_agent/nodes.py`

- Always `create_persistent_backend_factory` — **no** `get_session_sandbox_manager`, no `sandbox_backend`
- Base prompt: `FAST_SYSTEM_PROMPT` only (file/memory oriented)
- Middleware: SectionPrompt (MAIN + persona + skills + memory), MemoryIndex, ToolSearch, code interpreter, rubric, PromptCaching
- **No** `SANDBOX_*`, **no** `SandboxMCPMiddleware`, **no** `EnvVarPromptMiddleware`

Implication: image-capability injection scoped to “when sandbox is attached” will **not** affect Fast by design (matches current architecture).

### Prompt string sources (harness)

| Symbol | Defined in | Selected by |
|---|---|---|
| `SANDBOX_SYSTEM_PROMPT` | `search_agent/prompt.py`, also `team_agent/prompt.py` | `select_harness_text` / `AGENT_HARNESS_MODE` |
| `SANDBOX_RUNTIME_SECTION` | same | same; format `{work_dir}` at runtime |
| `DEFAULT_SYSTEM_PROMPT` | search only | non-sandbox |
| `FAST_SYSTEM_PROMPT` | fast | no sandbox path |

Harness mode is **import-time** for many prompt constants (`get_active_harness_mode()` at module load). `AGENT_HARNESS_MODE` is in `RESTART_REQUIRED_SETTINGS` (`src/kernel/config/constants.py`).

---

## Sandbox image/template config today

### Settings (definitions + defaults)

| Key | Platform | Default | Used as |
|---|---|---|---|
| `SANDBOX_PLATFORM` | all | `"daytona"` | select: daytona / e2b / opensandbox |
| `OPENSANDBOX_IMAGE` | opensandbox | `"ubuntu"` | container image name |
| `E2B_TEMPLATE` | e2b | `"base"` | E2B template |
| `DAYTONA_IMAGE` | daytona | `""` | snapshot name; empty → omit snapshot |

Sources:

- defaults: `src/kernel/config/base.py` (~189–219)
- UI metadata: `src/kernel/config/definitions.py` (~380–555), category `SettingCategory.SANDBOX`, per-platform `depends_on`
- hot-reload set: `src/kernel/config/service.py` `_SANDBOX_AFFECTED_SETTINGS` includes the three image/template keys (rebuilds `SessionSandboxManager` soft)

### Runtime consumers

- `OpenSandboxSandboxAdapter`: `settings.OPENSANDBOX_IMAGE` on create (`session_manager.py` ~236, 281, 372)
- `E2BSandboxAdapter`: `settings.E2B_TEMPLATE` (`session_manager.py` ~99, 118, 363)
- Daytona create: `snapshot=settings.DAYTONA_IMAGE if settings.DAYTONA_IMAGE else None` (~799)
- Factory configs: `src/infra/sandbox/base.py` `OpenSandboxConfig.image`, `E2BConfig.template`, `get_sandbox_config_from_settings()`

### Existing description / capability fields

**None found** for sandbox image/template capability text:

- No `*_IMAGE_DESCRIPTION`, `*_TEMPLATE_DESCRIPTION`, `SANDBOX_*DESC`, capability-bound prompt keys under sandbox settings
- No injection of image/template names into system prompt today (only work_dir + storage rules + mcporter + env keys)

Related-but-different:

- Persona “capability boundaries” i18n is for personas, not sandboxes
- `summarize_role_system_prompt` is team-role capability for the router, not image

### Setting type for multi-line text

`SettingType.TEXT` exists (`src/kernel/schemas/setting.py`) and renders as textarea. Precedent: `SESSION_TITLE_PROMPT` in `definitions.py`.

---

## Recommended injection design

### Options

| Option | Setting key shape | Injection mechanism | Pros | Cons |
|---|---|---|---|---|
| **A. Global one TEXT** | `SANDBOX_IMAGE_DESCRIPTION` (or `SANDBOX_CAPABILITY_DESCRIPTION`), category SANDBOX, `depends_on=ENABLE_SANDBOX` | Append into `_prompt_sections` next to runtime section via existing `SectionPromptMiddleware` | Matches PRD “global one copy”; works for all platforms; empty no-op; hot-readable from `settings` without new middleware | Not auto-switched when platform changes (admin maintains one blob) |
| **B. Per-platform TEXT** | `OPENSANDBOX_IMAGE_DESCRIPTION`, `E2B_TEMPLATE_DESCRIPTION`, `DAYTONA_IMAGE_DESCRIPTION` | Same section inject; pick by `SANDBOX_PLATFORM` | Aligns with image/template UI groups; can leave unused platforms empty | Three keys + i18n; still one active text at a time |
| **C. New middleware class** | Either A or B | `SandboxCapabilityMiddleware` like EnvVar | Symmetric with MCP/env | Overkill: value is static settings string, not async/user-scoped |
| **D. Bake into `SANDBOX_SYSTEM_PROMPT`** | Either A or B | Concatenate into base `system_prompt` | Single place | Breaks “stable base prompt” / harness import-time selection; couples free-form admin text with architecture contract; harder empty-skip; worse KV hygiene |

### Recommendation

1. **Prefer Option A** (`SANDBOX_IMAGE_DESCRIPTION`, `SettingType.TEXT`, default `""`, `frontend_visible=True`, category SANDBOX, `depends_on=ENABLE_SANDBOX`).  
   - If product insists descriptions travel with each image/template field in UI, use **B** but still inject only the active platform’s text.
2. **Inject via `SectionPromptMiddleware` section list**, not a new middleware and not into base `SANDBOX_SYSTEM_PROMPT`.
3. **Relative order vs `work_dir` section** (recommended):

   ```
   ... MAIN_AGENT / persona / skills / memory_guide ...
   [new] sandbox image capability section   # global, rarer-changing → before session path
   SANDBOX_RUNTIME_SECTION (work_dir)      # session-specific
   goal section
   → SandboxMCPMiddleware
   → EnvVarPromptMiddleware
   → ...
   ```

   Rationale: image capability is more stable than `work_dir`; keep dynamic/user content later for KV cache. Mark heading explicitly as environment capability boundary (e.g. `## Sandbox Environment Capabilities` / Chinese harness equivalent if templated).

4. **Empty behavior**: do not append when `settings.SANDBOX_IMAGE_DESCRIPTION.strip() == ""`. (`SectionPromptMiddleware` also strips empties, but gate at append site keeps intent clear and mirrors runtime gate.)
5. **Who gets it**:
   - Main agent of **search** and **team** when `sandbox_backend` is truthy
   - Subagents of both (same as runtime section) so delegated work sees the same capability boundary
   - **Not** fast_agent (no sandbox attach)
6. **Hot reload**: pure prompt text; **do not** add to `_SANDBOX_AFFECTED_SETTINGS` (that set rebuilds sandbox manager). Changing description should take effect on next agent graph build / next turn without sandbox recreate. (If sections are captured once at graph compile, next run rebuilds graph as current code already does per turn.)
7. **Do not** put secrets in the field (admin-authored free text; same trust model as other TEXT settings).
8. **Platform scope**: A covers OpenSandbox + E2B + Daytona symmetrically without waiting on per-platform product decisions. Minimum for PRD “at least OpenSandbox” is still satisfied by A or a single `OPENSANDBOX_*` key under B.

### Suggested section builder (design sketch only)

Keep builder next to other sandbox prompt strings (e.g. helper in `search_agent/prompt.py` or small `src/infra/sandbox/capability_prompt.py`) so both search and team import one function:

- Input: settings text
- Output: `""` or a short headed section string
- Optional harness variants only if product wants zh/en chrome around free-form admin body; body itself stays admin language

Team should keep importing from the shared place (today already reuses search_agent sandbox strings).

---

## Key files

| Path | Role |
|---|---|
| `src/agents/search_agent/prompt.py` | `SANDBOX_SYSTEM_PROMPT`, `SANDBOX_RUNTIME_SECTION`, `DEFAULT_SYSTEM_PROMPT` |
| `src/agents/search_agent/nodes.py` | Backend/prompt creation; main + subagent middleware order |
| `src/agents/team_agent/prompt.py` | Parallel sandbox strings (unused by nodes injection); team router prompts |
| `src/agents/team_agent/nodes.py` | Team sandbox attach; uses **search** sandbox strings; middleware stack |
| `src/agents/fast_agent/prompt.py` / `nodes.py` | No sandbox injection path |
| `src/infra/agent/middleware/prompt_injection.py` | `SectionPromptMiddleware`, `SandboxMCPMiddleware`, `EnvVarPromptMiddleware` |
| `src/infra/agent/middleware/__init__.py` | Public exports |
| `src/infra/tool/sandbox_mcp_prompt.py` | MCP list prompt builder + cache |
| `src/infra/tool/env_var_prompt.py` | Env key prompt builder + cache |
| `src/infra/sandbox/session_manager.py` | `get_or_create`, adapters for image/template |
| `src/infra/sandbox/base.py` | Config dataclasses + factory; `get_sandbox_config_from_settings` |
| `src/kernel/config/base.py` | Setting defaults (`OPENSANDBOX_IMAGE`, `E2B_TEMPLATE`, `DAYTONA_IMAGE`, …) |
| `src/kernel/config/definitions.py` | Setting UI metadata (SANDBOX category) |
| `src/kernel/config/service.py` | `_SANDBOX_AFFECTED_SETTINGS` hot reload |
| `src/kernel/config/constants.py` | `RESTART_REQUIRED_SETTINGS` (includes `AGENT_HARNESS_MODE`) |
| `src/kernel/schemas/setting.py` | `SettingType.TEXT` for textarea |
| `src/agents/core/subagent_prompts.py` | `MAIN_AGENT_PROMPT_SECTIONS`, `SUBAGENT_PROMPT` |
| `src/agents/core/harness_prompt_overrides.py` | `select_harness_text`, harness catalogs |
| `tests/agents/core/test_subagent_prompts.py` | Contracts: single `SectionPromptMiddleware`, runtime out of global prefix |

Key functions:

- `search_agent.nodes._create_backend_and_prompt`
- `SessionSandboxManager.get_or_create`
- `SectionPromptMiddleware.__init__` / `awrap_model_call`
- `build_sandbox_mcp_prompt_sections`
- `build_env_var_prompt_sections`
- `select_harness_text` / `get_active_harness_mode`
- `get_sandbox_config_from_settings`

---

## Open risks

1. **Dual SANDBOX_* definitions** in `team_agent/prompt.py` vs live `search_agent` imports — implement must touch the path nodes actually use (search_agent or a new shared module); updating only team_agent prompts will not inject.
2. **Subagent vs main asymmetry**: Sandbox MCP list is main-only; capability description should still go to subagents (like runtime), or workers may not know preinstalled limits.
3. **Graph compile capture**: sections are built when the node constructs middleware for that run. Confirm no long-lived agent reuses an old section list across settings edits mid-process (current per-run build looks safe; document if any cache appears later).
4. **Harness chrome around free text**: admin text is language-agnostic; wrapping headings via `select_harness_text` is optional. Avoid forcing restart by not baking free text into import-time constants.
5. **Fast agent**: out of scope for sandbox capability injection unless product later adds sandbox to Fast.
6. **No existing tests for image description** — new cases should mirror `test_sandbox_runtime_value_stays_out_of_global_prefix` and empty-section behavior; keep empty string ≡ no section.
7. **Per-platform UI binding**: if choosing Option A, UI may still show the field under general sandbox settings rather than next to `OPENSANDBOX_IMAGE`; product may prefer B for visual proximity.
8. **PRD open question** (OpenSandbox-only vs symmetric): codepaths for all three platforms already share the same prompt attach sites, so symmetric inject cost is low once a setting exists.

## Caveats / Not Found

- No prior `SANDBOX_IMAGE_DESCRIPTION` (or synonym) in codebase.
- Did not re-audit frontend settings form component tree beyond knowing definitions drive visibility (`frontend_visible`, `depends_on`, `SettingType.TEXT`).
- Did not re-verify FastAgentContext tool loading of sandbox_mcp management tools (historical note from archived research); Fast **nodes** still do not attach sandbox backend/prompt layers.
