# Research: AGENT_HARNESS_MODE UI & Hot-Reload Semantics

- **Query**: Clarify `AGENT_HARNESS_MODE` config, restart/hot-reload semantics, SettingsPanel SELECT rendering, Agent-related UI placement options, compact_zh coverage
- **Scope**: mixed (internal codebase + existing spec)
- **Date**: 2026-07-21

## Summary

- `AGENT_HARNESS_MODE` is a global SELECT setting (`legacy | compact_en | compact_zh`), **default `compact_zh`**.
- It currently lives under **Settings → LLM → subcategory `cache`**, not under Agent category or Agent panels.
- Spec and implementation both treat it as **startup-only / restart-required**: listed in `RESTART_REQUIRED_SETTINGS`; mode is snapshotted at **module import** into module-level constants and deepagents `HarnessProfile` registration.
- `refresh_settings()` can update the in-memory `settings.AGENT_HARNESS_MODE`, but **does not re-bind** import-time harness constants / profile registration → changing the value at runtime has no effective prompt effect until process restart.
- Frontend already can render this as a GlassSelect dropdown via generic SettingsPanel SELECT handling; i18n description already says restart required. There is **no dedicated restart badge/toast** wired to `requires_restart` in the Settings UI save path.
- Best low-effort move for R3: change definition `category` to `SettingCategory.AGENT` (and optionally a clearer subcategory), keep same key/API; optionally add a small SELECT card inside AgentModelPanel that still calls `settingsApi`.

---

## Backend config & restart semantics

### Type & helpers

| Symbol | Location | Notes |
|---|---|---|
| `HarnessMode` | `src/kernel/config/base.py:30` | `Literal["legacy", "compact_en", "compact_zh"]` |
| `VALID_HARNESS_MODES` | `base.py:31` | frozenset of three modes |
| `normalize_harness_mode` | `base.py:34-39` | strip + lower; raises `ValueError` with allowed list |
| `Settings.AGENT_HARNESS_MODE` | `base.py:92` | field default `"compact_zh"` |
| field validator | `base.py:455-458` | normalizes string input before Literal check |
| `get_active_harness_mode()` | `base.py:515-516` | `normalize_harness_mode(settings.AGENT_HARNESS_MODE)` |
| re-exports | `src/kernel/config/__init__.py` | `HarnessMode`, `get_active_harness_mode`, `RESTART_REQUIRED_SETTINGS` |

Spec contract: `.trellis/spec/backend/agent-harness.md` — mode helpers live in `src.kernel.config`; infra must not import agents just to read mode; **`AGENT_HARNESS_MODE` is startup-only and requires restart**.

### SETTING_DEFINITIONS entry

```python
# src/kernel/config/definitions.py:208-215
"AGENT_HARNESS_MODE": {
    "type": SettingType.SELECT,
    "category": SettingCategory.LLM,
    "subcategory": "cache",
    "description": "settingDesc.AGENT_HARNESS_MODE",
    "default": "compact_zh",
    "options": ["legacy", "compact_en", "compact_zh"],
},
```

- **No `frontend_visible: True`** → only users with `settings:manage` (admin_mode) receive it in `GET /api/settings/` (`storage.py` filters on `frontend_visible` for non-admin).
- **No special depends_on**.

### RESTART_REQUIRED_SETTINGS

```python
# src/kernel/config/constants.py:12-35
RESTART_REQUIRED_SETTINGS = {
    ...
    # Harness profiles register at import time.
    "AGENT_HARNESS_MODE",
    # OTEL ...
    "TRACING_PROVIDER",
    ...
}
```

`SettingsStorage` stamps `requires_restart=key in RESTART_REQUIRED_SETTINGS` on every `SettingItem` (`storage.py:90`, `139`).  
`SettingsService.requires_restart(key)` (`service.py:255-257`) is used by the settings PUT handler.

### Settings API read/write path

| Endpoint | File | Behavior |
|---|---|---|
| `GET /api/settings/` | `src/api/routes/settings.py` | Grouped by category; admin vs non-admin filter |
| `GET /api/settings/{key}` | same | single item, requires `settings:manage` |
| `PUT /api/settings/{key}` | same | `service.set` → `refresh_settings(key)` → returns `SettingUpdateResponse` with `requires_restart` + message text |
| `POST /api/settings/reset[/{key}]` | same | reset + refresh |

On update success message when restart required:
`"Setting updated. Server restart required to take effect."` (`settings.py:63-72`).

Frontend client (`frontend/src/services/api/settings.ts`) types `update()` as returning `SettingItem` only — **it does not surface `SettingUpdateResponse.requires_restart` / message**. `useSettings.updateSetting` discards the response body after refetch.

### Hot reload reality (why restart is required)

**Layer A — live settings object**

- `initialize_settings()` (lifespan, after process start) loads DB into global `settings` (`service.py:257-293`).
- `refresh_settings(key)` after admin PUT updates `settings.<KEY>` and some specialized caches (LLM model cache, memory, sandbox, etc.). **No harness-specific rebind branch exists** in `refresh_settings`.

**Layer B — import-time / startup-only harness binding**

These capture mode once when the module is first imported:

| Site | Binding style |
|---|---|
| `src/agents/core/persona.py:32` | `_HARNESS_MODE = get_active_harness_mode()`; builds `_BEHAVIOR_GUIDE`; **registers `HarnessProfile` for anthropic/openai/google_genai** with tool overrides + `extra_middleware` lambda (`persona.py:107-127`) |
| `src/agents/core/harness_prompt_overrides.py:27-29` | `select_harness_text` **re-reads** `get_active_harness_mode()` at call time |
| `src/agents/core/harness_prompt_overrides.py:330-343` | module-level `_mode`, `TOOL_DESCRIPTION_OVERRIDES`, `SHORT_*` constants frozen at import |
| `src/agents/core/subagent_prompts.py:18` + many `select_harness_text(...)` at module body | prompt string constants frozen at import |
| `src/agents/fast_agent/prompt.py`, `search_agent/prompt.py`, `team_agent/prompt.py` | `*_SYSTEM_PROMPT` / sandbox sections via `select_harness_text` at import |
| `src/agents/team_agent/prompt.py:7` | `_HARNESS_MODE` for label localization helpers |
| `src/infra/tool/deferred_manager.py:20-48` | `_HARNESS_MODE` + `DEFERRED_TOOL_SEARCH_GUIDE` frozen at import |

Critical: even though `select_harness_text` itself is live, almost all call sites assign its result to **module-level constants** once. `persona.register_harness_profile` also runs only at import.  
Comment in `constants.py` matches: *"Harness profiles register at import time."*

**Import order note**: agent discovery/warmup runs **after** `initialize_settings()` in app lifespan (`main.py`), so first registration sees DB-backed mode. Runtime admin changes still require restart.

### Catalog / middleware helpers (runtime use of mode)

```python
# harness_prompt_overrides.py
select_harness_text(*, legacy, compact_en, compact_zh)  # live read
catalog_for_mode(mode)                                  # builds HarnessCatalog
build_short_todo_middleware(mode=None)
build_harness_extra_middleware(mode=None)               # used from persona extra_middleware lambda
localize_tool_for_model(tool, catalog)                  # model-view only; runtime tools unchanged
```

`build_harness_extra_middleware` is passed as `lambda: build_harness_extra_middleware(_HARNESS_MODE)` with the **import-time** `_HARNESS_MODE` closed over — not a live re-read of settings.

### Test anchors

- `tests/agents/core/test_harness_prompt_overrides.py` — definition options/default, presence in `RESTART_REQUIRED_SETTINGS`, normalize, middleware, isolated-process mode tests.
- Spec tests listed in `agent-harness.md` §6.

---

## Frontend settings rendering

### Data model

`frontend/src/types/settings.ts`:

- `SettingType` includes `"select"`.
- `SettingItem` has `options?: string[]`, `requires_restart: boolean`, `category`, `subcategory`, `description` (i18n key).
- `SettingCategory` includes `"agent"` and `"llm"`.

### SettingsPanel flow

File: `frontend/src/components/panels/SettingsPanel.tsx`  
Constants: `SettingsPanel.constants.ts` (`CATEGORY_ORDER` places `"agent"` before `"llm"`).

1. Loads settings via `useSettingsContext` → `settingsApi.list()`.
2. Sidebar/tabs by `CATEGORY_ORDER`; within category groups by `subcategory`.
3. Subcategory labels from `t("subcategories.<name>")` — **`cache` exists** (`zh`: "缓存", `en`: "Cache").
4. SELECT detection (`SettingsPanel.tsx:698-704`):

```ts
const isSelect =
  setting.key === "DEFAULT_AGENT" ||
  setting.key === "DEFAULT_USER_ROLE" ||
  MODEL_CONFIG_SETTING_KEYS.has(setting.key) ||
  setting.key in MODEL_CARD_KIND_FILTER ||
  setting.type === "boolean" ||
  (setting.type === "select" && setting.options);
```

For `AGENT_HARNESS_MODE`, last clause matches → **GlassSelect** with `setting.options.map(opt => ({ value: opt, label: opt }))` — raw option strings (`legacy` / `compact_en` / `compact_zh`), **no per-option i18n labels**.

5. Description shown as `t(setting.description)` → `settingDesc.AGENT_HARNESS_MODE`.
6. **`requires_restart` is never rendered** as a badge/icon in the card; only the description text mentions restart.
7. Save path does not toast the API restart message.

### Current UI location of harness

- System Settings panel → category **LLM** → subcategory **cache**, next to `LLM_MODEL_CACHE_SIZE`, `PROMPT_CACHE_MAX_*`.
- Not on AgentConfigPanel / AgentModelPanel / UserAgentPreferencePanel.

### i18n keys (harness)

| Locale | Key | Value |
|---|---|---|
| zh | `settingDesc.AGENT_HARNESS_MODE` | 代理 Harness 语言/压缩模式（需重启） |
| en | same | Agent harness language/compression mode (restart required) |

Category labels: `categories.agent` / `categories.llm` present. Subcategory `cache` present.

---

## Agent panel placement options (pros/cons + recommendation)

### Existing Agent-related UIs

| UI | Path / tab | Audience | Purpose today |
|---|---|---|---|
| **AgentModelPanel** | `agents` tab → `AgentSection` + `ModelSection` | Agent admin (`AGENT_ADMIN`) + model mgmt | Catalog enable/order + role→agent mapping + model cards |
| **AgentConfigPanel** | legacy / re-exported; logic mirrored by AgentSection | same | Global agents + roles tabs |
| **UserAgentPreferencePanel** | profile | end user | Per-user default agent preference |
| **SettingsPanel → agent category** | `settings` tab, category `agent` | `settings:manage` | Only `APP_BASE_URL`, `DEBUG`, `LOG_LEVEL` (misc app ops) |
| **SettingsPanel → llm/cache** | current home of harness | `settings:manage` | LLM retries/cache + **AGENT_HARNESS_MODE** |

AgentModelPanel is the primary "代理" surface in TabContent (`agents: AgentModelPanel`).

### Option A — Only re-bucket definition to `SettingCategory.AGENT`

- Change `definitions.py` category to `SettingCategory.AGENT`, subcategory e.g. `general` or new `harness`.
- Pros: zero new frontend components; SettingsPanel SELECT already works; stays on same settings API/permissions; category closer to "代理"; restart semantics unchanged.
- Cons: still buried in system Settings (not AgentModelPanel); `agent` category today mixes host/debug logs, so semantic fit is imperfect unless subcategory is dedicated; non-admin still cannot see it.

### Option B — Dedicated control in AgentModelPanel (still settings API)

- Add a small admin-only card/dropdown in `AgentSection` (or a third mini-section) calling `settingsApi.get/update("AGENT_HARNESS_MODE")`.
- Pros: literally under "代理" UX; can add option labels + explicit restart callout; matches PRD "贴近代理配置".
- Cons: new UI code + permission check (`settings:manage` vs `AGENT_ADMIN` mismatch risk); duplicates control if still also in SettingsPanel unless hidden/moved.

### Option C — A only + soft deeplink from Agent panel

- Re-bucket to agent category; optional "open system settings" hint in AgentModelPanel.
- Pros: tiny; single source of truth remains Settings.
- Cons: extra click; weaker "入口在代理区" feel.

### Option D — Custom Agent API (not recommended)

- New agent-config endpoint writing the same setting.
- Pros: none material.
- Cons: second write path; still restart-bound; more surface area.

### Recommendation (for implementer)

1. **Minimum for R3**: Option A — move definition to `SettingCategory.AGENT` + subcategory `general` (or `harness` if i18n subcategory key added). Keep `RESTART_REQUIRED_SETTINGS` and description restart wording. Optionally add option label i18n later.
2. **Stronger UX if product wants literal Agent tab entry**: Option B, admin-gated by `settings:manage` (or both permissions), with explicit restart notice; keep definition category = agent so Settings still shows one source of truth.
3. Do **not** put harness on `UserAgentPreferencePanel` (per-user; wrong scope — PRD says global).
4. Do **not** attempt true hot-reload in this task without a full rebind design (profile re-register, module constant refresh, multi-worker safety).

---

## Chinese compact_zh coverage notes

### Covered (compact_zh present)

| Area | Mechanism |
|---|---|
| Behavior guide | `COMPACT_ZH_BEHAVIOR_GUIDE` |
| Tool descriptions (task/ls/read/write/edit/glob/grep/execute/search_tools/write_todos) | `_ZH_TOOLS` via catalog |
| Schema field titles | `_ZH_FIELDS` |
| write_todos / memory / filesystem / execute / task system snippets | `catalog_for_mode` |
| available agents heading | `"可用代理类型："` |
| Fast / Search / Team system prompts & sandbox sections | `select_harness_text(..., compact_zh=...)` |
| Subagent prompts, file/workspace/safety/tool-discovery guides | `subagent_prompts.py` |
| Persona default role | Chinese when compact_zh |
| Deferred MCP guide | `deferred_manager.py` compact_zh branch |

Default product mode is **`compact_zh`** (Settings field default + definition default).

### Intentional English / bilingual leftovers (not missing translations of UI)

These are **protocol/format strings** or **tool machine-facing English** that remain even in compact_zh:

| Item | Where | Note |
|---|---|---|
| `Current task start time: YYYY-MM-DD HH:mm:ss ±HH:MM Timezone` | ZH tool `task` description + subagent guides | Fixed English format token for parsing consistency |
| Heading `## MCP Tools (Deferred)` | deferred_manager prompt builder | Section title kept English in ZH guide text as well |
| Tool **names** / property names / enums | catalog contract | Must stay English across modes (spec) |
| `VENDOR_AVAILABLE_AGENTS_HEADING = "Available subagent types:"` | harness_prompt_overrides | Vendor pin string; ZH uses separate `available_agents_heading` in catalog |
| Status tokens `pending` / `in_progress` / `completed` | ZH write_todos text | Machine status values |

### True English-only model-facing surface (legacy / uncatalogued)

| Item | Where | Mode impact |
|---|---|---|
| `ToolSearchTool.description` default class field | `src/infra/tool/tool_search_tool.py:55-70` | Long English description on the live tool object. Compact modes rely on **`HarnessLocalizationMiddleware` + catalog `search_tools`** to override model-view description; legacy keeps vendor/this English. Not a missing compact_zh catalog entry (`_ZH_TOOLS["search_tools"]` exists). |
| Various `Error: ...` / search result English strings inside tool `_arun` | same file | Runtime tool messages to model; not mode-switched |

### Frontend i18n for this setting

- zh/en descriptions present for `AGENT_HARNESS_MODE`.
- Option values themselves are technical enums shown raw in GlassSelect (all locales).
- ja/ko/ru: not checked for this key in this pass; if `settingDesc.AGENT_HARNESS_MODE` missing there, i18n falls back to key or en depending on project fallback config.

### Coverage conclusion for PRD "全套中文 compact_zh"

Catalog + first-party agent/subagent/sandbox prompts for compact_zh appear **intentionally complete**. Residual English is either (a) required machine schema/format, (b) deferred-section title convention, or (c) base tool object English overridden at model-view layer in compact modes. **No large Chinese gap in the harness catalog** for this task’s scope; R4 says not to rework catalog unless missing.

---

## Key files

### Backend

| Path | Role |
|---|---|
| `src/kernel/config/base.py` | HarnessMode, normalize, Settings field, get_active_harness_mode |
| `src/kernel/config/definitions.py` | SETTING_DEFINITIONS entry (LLM/cache SELECT) |
| `src/kernel/config/constants.py` | RESTART_REQUIRED_SETTINGS includes AGENT_HARNESS_MODE |
| `src/kernel/config/service.py` | initialize_settings / refresh_settings |
| `src/kernel/schemas/setting.py` | SettingType.SELECT, SettingCategory.AGENT/LLM, SettingItem.requires_restart, SettingUpdateResponse |
| `src/infra/settings/storage.py` | builds SettingItem + options + requires_restart |
| `src/infra/settings/service.py` | set/get/requires_restart; refresh after write |
| `src/api/routes/settings.py` | REST API; restart message on PUT |
| `src/agents/core/harness_prompt_overrides.py` | select_harness_text, catalog, middleware, import-time SHORT_* |
| `src/agents/core/persona.py` | import-time profile registration |
| `src/agents/core/subagent_prompts.py` | import-time prompt selection |
| `src/agents/fast_agent/prompt.py` | FAST_SYSTEM_PROMPT selection |
| `src/agents/search_agent/prompt.py` | search/sandbox prompts |
| `src/agents/team_agent/prompt.py` | team/sandbox prompts + labels |
| `src/infra/tool/deferred_manager.py` | import-time deferred guide language |
| `.trellis/spec/backend/agent-harness.md` | contract: startup-only mode |

### Frontend

| Path | Role |
|---|---|
| `frontend/src/components/panels/SettingsPanel.tsx` | category/subcategory grouping + SELECT GlassSelect |
| `frontend/src/components/panels/SettingsPanel.constants.ts` | CATEGORY_ORDER |
| `frontend/src/types/settings.ts` | SettingItem / categories |
| `frontend/src/services/api/settings.ts` | list/get/update (ignores update restart payload typing) |
| `frontend/src/hooks/useSettings.ts` | update + refetch |
| `frontend/src/i18n/locales/zh.json` / `en.json` | `settingDesc.AGENT_HARNESS_MODE` |
| `frontend/src/components/panels/AgentModelPanel/*` | agents tab host (AgentSection / ModelSection) |
| `frontend/src/components/panels/AgentPanel/AgentConfigPanel.tsx` | agent catalog UI (no settings) |
| `frontend/src/components/profile/UserAgentPreferencePanel.tsx` | per-user agent only |
| `frontend/src/components/layout/AppContent/TabContent.tsx` | `settings` → SettingsPanel, `agents` → AgentModelPanel |

### Tests

| Path | Role |
|---|---|
| `tests/agents/core/test_harness_prompt_overrides.py` | mode validation, RESTART_REQUIRED, middleware |

---

## Implementation notes

1. **Do not remove from `RESTART_REQUIRED_SETTINGS`** unless also implementing full rebind of: persona HarnessProfile registration, all module-level `select_harness_text` constants, deferred_manager guide, and multi-worker workers. Spec forbids treating mode as hot without that.
2. **Moving category alone is sufficient** for "appear under 代理 category in system settings": edit `definitions.py` only (`SettingCategory.AGENT`). Subcategory options:
   - reuse `general` (already i18n’d; sits with APP_BASE_URL/DEBUG/LOG_LEVEL)
   - add `harness` + `subcategories.harness` i18n if clearer grouping desired
3. **UI restart disclosure today**: description string only. If implementing Agent panel control, show explicit restart notice; optionally also badge any `requires_restart` item in SettingsPanel (shared improvement).
4. **Permissions**: harness is admin settings (`settings:manage`). AgentModelPanel uses `AGENT_ADMIN` for catalog — if Option B, gate harness editor with `settings:manage` (or require both) so non-settings admins cannot flip process-global mode.
5. **Default remains `compact_zh`** — no change needed for "主力中文".
6. **Option labels**: raw enums are OK; nicer labels could be `settings.harnessMode.legacy|compact_en|compact_zh` i18n without changing stored values.
7. **Frontend type gap**: `settingsApi.update` should ideally return `SettingUpdateResponse` if UI wants toast of API restart message; currently unused.
8. **Not in scope for this research**: sandbox image capability description (R1/R2) — separate research topic if needed.
9. **Hot-reload evaluation answer for PRD R3**: current implementation **must restart**; UI already states that in description; true hot update is a larger design and not free.

## Caveats / Not Found

- Did not run runtime process or hit live API; conclusions from static code + tests.
- ja/ko/ru completeness for `settingDesc.AGENT_HARNESS_MODE` not exhaustively verified.
- Whether deepagents `register_harness_profile` is idempotent for re-register after mode flip was not proven; code assumes import-once registration.
- No existing dedicated harness UI component outside SettingsPanel.
