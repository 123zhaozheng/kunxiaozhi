# Research: Settings enum + depends_on UI (TRACING_PROVIDER design)

- **Query**: How LambChat implements settings enums + depends_on for admin dropdowns (SANDBOX_PLATFORM pattern); exact fields for TRACING_PROVIDER + LANGSMITH_*/PHOENIX_*; i18n; hot-reload for tracing
- **Scope**: internal
- **Date**: 2026-07-20

## Findings

### Files Found

| File Path | Description |
|---|---|
| `src/kernel/schemas/setting.py` | `SettingType`, `SettingDependsOn`, `SettingCategory`, `SettingItem` API schemas |
| `src/kernel/config/definitions.py` | Single source of truth for most settings incl. `SANDBOX_PLATFORM` + all `LANGSMITH_*` |
| `src/kernel/config/_definitions_extra.py` | Extra defs: `S3_PROVIDER`, `CHECKPOINT_BACKEND`, `DIFY_KB_SEARCH_METHOD` SELECTs |
| `src/kernel/config/base.py` | Runtime `Settings` pydantic model; LANGSMITH → `os.environ` sync in `__init__` only |
| `src/kernel/config/constants.py` | `RESTART_REQUIRED_SETTINGS` (tracing keys not listed) |
| `src/kernel/config/service.py` | `initialize_settings` / `refresh_settings`; sandbox/checkpoint hot-reload sets; **no tracing re-init** |
| `src/infra/settings/storage.py` | Maps definition → `SettingItem` (`depends_on`, `options`); SELECT validation |
| `src/infra/settings/service.py` | Admin set/reset → `refresh_settings(key)` |
| `src/infra/tracing/langsmith_client.py` | Lazy singleton tracer; reads `os.getenv` / `settings` once |
| `src/infra/tracing/decorators.py` | `@traced` gates on `LANGSMITH_TRACING` env at **decoration** time |
| `frontend/src/types/settings.ts` | Frontend `SettingType`, `SettingDependsOn`, `SettingItem` |
| `frontend/src/components/panels/SettingsPanel.tsx` | Visibility (`isSettingVisible`) + SELECT rendering |
| `frontend/src/components/panels/SettingsPanel.constants.ts` | `CATEGORY_ORDER` includes `"tracing"` |
| `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json` | `settingDesc.*`, `categories.tracing`, `subcategories.langsmith` |

---

### 1. SettingType for enum/select

**Backend enum** (`src/kernel/schemas/setting.py:18-26`):

```python
class SettingType(str, Enum):
    STRING = "string"
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"
    SELECT = "select"  # Dropdown select (uses options field)
```

**Dependency model** (`src/kernel/schemas/setting.py:11-15`, `97-100`):

```python
class SettingDependsOn(BaseModel):
    key: str   # Parent setting key
    value: Any # Expected value for visibility

# On SettingItem:
depends_on: Optional[Union[str, SettingDependsOn]] = None
options: Optional[list[str]] = None  # for SELECT
```

**Frontend mirror** (`frontend/src/types/settings.ts:5-11`, `41-45`, `68-81`):

- `SettingType` includes `"select"`
- `SettingDependsOn { key, value }`
- `depends_on?: string | SettingDependsOn`
- `options?: string[]`

**Category** already has `TRACING = "tracing"` (`src/kernel/schemas/setting.py:48`; frontend `SettingCategory` includes `"tracing"` at `frontend/src/types/settings.ts:29`).

---

### 2. SELECT + depends_on definition pattern (canonical: SANDBOX_PLATFORM)

**Master toggle + platform SELECT** (`src/kernel/config/definitions.py:364-379`):

```python
"ENABLE_SANDBOX": {
    "type": SettingType.BOOLEAN,
    "category": SettingCategory.SANDBOX,
    "subcategory": "general",
    "description": "settingDesc.ENABLE_SANDBOX",
    "default": False,
    "frontend_visible": True,
},
"SANDBOX_PLATFORM": {
    "type": SettingType.SELECT,
    "category": SettingCategory.SANDBOX,
    "subcategory": "general",
    "description": "settingDesc.SANDBOX_PLATFORM",
    "default": "daytona",
    "depends_on": "ENABLE_SANDBOX",          # string form → boolean parent
    "options": ["daytona", "e2b", "opensandbox"],
},
```

**Child keys use object depends_on** (`definitions.py:381-546` pattern):

```python
"DAYTONA_API_KEY": {
    "type": SettingType.STRING,
    "category": SettingCategory.SANDBOX,
    "subcategory": "daytona",
    "description": "settingDesc.DAYTONA_API_KEY",
    "default": "",
    "is_sensitive": True,
    "depends_on": {"key": "SANDBOX_PLATFORM", "value": "daytona"},
},
# E2B_* → value "e2b"; OPENSANDBOX_* → value "opensandbox"
```

**Other SELECT examples**:

| Key | File:line | options | depends_on |
|---|---|---|---|
| `TASK_BACKEND` | `definitions.py:915-921` | `["local","arq"]` | ARQ_* use `{"key":"TASK_BACKEND","value":"arq"}` |
| `S3_PROVIDER` | `_definitions_extra.py:127-134` | aws/aliyun/tencent/minio/custom | `"S3_ENABLED"` (string) |
| `CHECKPOINT_BACKEND` | `_definitions_extra.py:350-355` | mongodb/postgres | PG_* → value `"postgres"` |
| `DIFY_KB_SEARCH_METHOD` | `_definitions_extra.py:851-857` | hybrid/semantic/keyword/full_text | `"DIFY_KB_ENABLED"` |

**API surface** (`src/infra/settings/storage.py:82-95`, `171-175`):

- Emits `depends_on` and `options` from definition dict as-is
- On set: if type is `select` and `options` non-empty, value must be in `options`

**Runtime Settings fields** live on `Settings` in `base.py` (e.g. `SANDBOX_PLATFORM: str = "daytona"` at `base.py:179`; LANGSMITH at `base.py:216-220`). New keys need matching attributes on `Settings` or `setattr` in refresh still works only if `hasattr` is true for load (`service.py:289-290`).

---

### 3. Current TRACING / LangSmith settings

**Definitions** (`src/kernel/config/definitions.py:964-1006`):

| Key | type | category | subcategory | depends_on | notes |
|---|---|---|---|---|---|
| `LANGSMITH_TRACING` | BOOLEAN | TRACING | langsmith | — | master switch |
| `LANGSMITH_API_KEY` | STRING | TRACING | langsmith | `"LANGSMITH_TRACING"` | `is_sensitive: True` |
| `LANGSMITH_PROJECT` | STRING | TRACING | langsmith | `"LANGSMITH_TRACING"` | default `"lamb-agent"` |
| `LANGSMITH_API_URL` | STRING | TRACING | langsmith | `"LANGSMITH_TRACING"` | default smith URL |
| `LANGSMITH_SAMPLE_RATE` | NUMBER | TRACING | langsmith | `"LANGSMITH_TRACING"` | default `1.0` |

None of these set `frontend_visible: True` → visible only in admin settings (`storage.py:62` admin_mode path). That matches admin panel usage.

**No `TRACING_PROVIDER` exists today.** Enable is purely `LANGSMITH_TRACING` boolean; children only depend on that boolean string form.

---

### 4. Frontend rendering: depends_on + SELECT dropdown

**Visibility** (`SettingsPanel.tsx:210-247`):

| `depends_on` shape | Visibility rule |
|---|---|
| missing | always visible |
| `string` (e.g. `"LANGSMITH_TRACING"`, `"ENABLE_SANDBOX"`) | parent value must be **`=== true`** (boolean) |
| `{ key, value }` (e.g. SANDBOX_PLATFORM / daytona) | parent value must be **`=== expectedValue`** |

Uses `editValues[parent]` when present (live edit), else `parentSetting.value`. Parent not found → show (lenient).

**SELECT UI** (`SettingsPanel.tsx:697-703`, `761-896`):

- Generic enum dropdown when `setting.type === "select" && setting.options`
- Options rendered as `{ value: opt, label: opt }` (raw string labels; **no** option-level i18n)
- `GlassSelect` path also covers boolean (true/false), model IDs, agents, roles

**Category / subcategory chrome**:

- `CATEGORY_ORDER` includes `"tracing"` (`SettingsPanel.constants.ts:28`)
- Label: `t("categories.tracing")` (`SettingsPanel.tsx:86`)
- Subcategory `langsmith`: `t("subcategories.langsmith")` (`SettingsPanel.tsx:185`)
- Missing subcategory key falls back to raw string (`SettingsPanel.tsx:297`)

No frontend code special-cases tracing beyond subcategory label map.

---

### 5. Exact fields for new TRACING_PROVIDER (+ children)

Mirror `SANDBOX_PLATFORM` / `TASK_BACKEND` object-depends pattern.

#### A. Recommended definition shape (settings layer only)

```python
# Master provider enum (new)
"TRACING_PROVIDER": {
    "type": SettingType.SELECT,
    "category": SettingCategory.TRACING,
    "subcategory": "general",  # or keep under provider-specific subs
    "description": "settingDesc.TRACING_PROVIDER",
    "default": "none",  # or "langsmith" if migrating enabled deployments
    "options": ["none", "langsmith", "phoenix"],
    # optional: "frontend_visible": True  only if non-admin should see it
},

# Existing LANGSMITH_* — change depends_on from string LANGSMITH_TRACING
# to object form (and keep or retire LANGSMITH_TRACING):
"LANGSMITH_API_KEY": {
    ...,
    "depends_on": {"key": "TRACING_PROVIDER", "value": "langsmith"},
},
"LANGSMITH_PROJECT":  { ..., "depends_on": {"key": "TRACING_PROVIDER", "value": "langsmith"} },
"LANGSMITH_API_URL":  { ..., "depends_on": {"key": "TRACING_PROVIDER", "value": "langsmith"} },
"LANGSMITH_SAMPLE_RATE": { ..., "depends_on": {"key": "TRACING_PROVIDER", "value": "langsmith"} },

# Future PHOENIX_* (illustrative field set; names not yet in codebase)
"PHOENIX_API_KEY": {
    "type": SettingType.STRING,
    "category": SettingCategory.TRACING,
    "subcategory": "phoenix",
    "description": "settingDesc.PHOENIX_API_KEY",
    "default": "",
    "is_sensitive": True,
    "depends_on": {"key": "TRACING_PROVIDER", "value": "phoenix"},
},
"PHOENIX_PROJECT":  { ..., "depends_on": {"key": "TRACING_PROVIDER", "value": "phoenix"} },
"PHOENIX_ENDPOINT": { ..., "depends_on": {"key": "TRACING_PROVIDER", "value": "phoenix"} },
# etc.
```

#### B. Definition field checklist (per key)

| Field | Required | Notes |
|---|---|---|
| `type` | yes | `SettingType.SELECT` for provider; STRING/NUMBER/BOOLEAN for children |
| `category` | yes | `SettingCategory.TRACING` |
| `subcategory` | yes | `"general"` for provider; `"langsmith"` / `"phoenix"` for children (UI grouping) |
| `description` | yes | i18n key `settingDesc.<KEY>` |
| `default` | yes | provider: one of `options`; children: sensible defaults |
| `options` | SELECT only | plain string list; validated on write |
| `depends_on` | children | **object** `{key, value}` for enum parent; **string** only for boolean parents |
| `is_sensitive` | keys/secrets | API masks as `********` |
| `frontend_visible` | optional | default `False` → admin-only |
| Matching `Settings` attr in `base.py` | yes | required for `initialize_settings` to load into global `settings` |

#### C. Migration notes for `LANGSMITH_TRACING`

Two compatible UI designs:

1. **Replace boolean with SELECT** (`none` | `langsmith` | `phoenix`): drop or deprecate `LANGSMITH_TRACING`; map old `true` → `"langsmith"`, `false` → `"none"`.
2. **Keep boolean enable + SELECT provider**:  
   - `LANGSMITH_TRACING` (or rename `ENABLE_TRACING`) string-depends for provider SELECT  
   - children object-depend on provider value  
   - Matches ENABLE_SANDBOX + SANDBOX_PLATFORM nesting.

String `depends_on` **cannot** express “provider == langsmith”; object form is required for multi-value parents (`SettingsPanel.tsx:233-243`).

#### D. Runtime attribute + env (if keeping LangSmith SDK env contract)

Today `Settings.__init__` (`base.py:419-429`) copies LANGSMITH_* into `os.environ` once. Any new provider must either:

- continue writing env for the active provider at start / on refresh, and/or  
- read `settings.TRACING_PROVIDER` / keys directly in tracing code (preferred for multi-provider).

---

### 6. i18n pattern (`settingDesc.*`)

- Backend stores **i18n key string**, not translated text: `"description": "settingDesc.LANGSMITH_TRACING"` (`definitions.py:971`).
- Frontend renders `t(setting.description)` (`SettingsPanel.tsx:737`).
- Locale files under `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json` → top-level object `settingDesc`:

  - en: `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / … (~lines 2030-2034)
  - zh same keys (~2030-2034)
  - also `SANDBOX_PLATFORM` etc.

- Category: `categories.tracing` (en ~589: `"Tracing"`).
- Subcategory: `subcategories.langsmith` (en ~2488: `"LangSmith"`). For Phoenix add `subcategories.phoenix` in all 5 locales + optional entry in `SUBCATEGORY_LABELS` (`SettingsPanel.tsx:171+`); without map entry UI falls back to raw `"phoenix"`.
- **Option labels are not i18n’d** — dropdown shows raw option strings (`daytona`, `langsmith`, …).

New keys to add for design A:

- `settingDesc.TRACING_PROVIDER`
- `settingDesc.PHOENIX_*` (as keys are added)
- optionally `subcategories.phoenix`, `subcategories.general` already exists

---

### 7. Hot-reload: can runtime setting changes re-init tracing?

**Short answer: No effective hot-reload for tracing today. Process start (or first tracer use with stale env) dominates.**

| Layer | Behavior | Anchor |
|---|---|---|
| Admin save | `SettingsService.set` → `refresh_settings(key)` | `service.py` (infra) ~132-134 |
| `refresh_settings` | `setattr(settings, key, value)` into global Settings | `config/service.py:331-340` |
| Hot-reload special cases | LLM cache, memory backend, checkpoint reset, **sandbox** rebuild only | `config/service.py:317-358`, `_SANDBOX_AFFECTED_SETTINGS` 48-69 |
| Tracing | **Not** in any `_*_AFFECTED_SETTINGS`; **no** `os.environ` re-sync on refresh | — |
| Env sync | Only in `Settings.__init__` when process constructs Settings | `base.py:419-429` |
| `LangSmithTracer` | Lazy once: if `self._enabled is not None: return` | `langsmith_client.py:26-38` |
| `@traced` decorator | Reads `os.getenv("LANGSMITH_TRACING")` at **decorate/import** time; disabled → returns bare func forever | `decorators.py:24-26` |
| `RESTART_REQUIRED_SETTINGS` | Does **not** list LANGSMITH_* | `constants.py:12-28` |

Implications for Phoenix work:

1. Changing LANGSMITH_* in admin updates DB + in-memory `settings` attrs, but **does not** update `os.environ` used by SDK / `@traced` / parts of tracer.
2. Global `tracer` never re-initializes after first use.
3. `@traced` is especially sticky (import-time gate).
4. Sandbox is the reference for soft runtime rebuild (`_SANDBOX_AFFECTED_SETTINGS` + `_reset_sandbox_runtime_state`); tracing would need an analogous `_TRACING_AFFECTED_SETTINGS` + re-sync env + reset tracer singleton if runtime switch is a product goal. Without that, document “restart required” for provider flips even if keys are not in `RESTART_REQUIRED_SETTINGS`.

---

### Related Specs

- No dedicated `.trellis/spec` file for settings enum/depends_on found in this pass; task PRD at `.trellis/tasks/07-20-phoenix-langsmith-tracing-provider/prd.md` (not re-read here).

## Caveats / Not Found

- `TRACING_PROVIDER` / `PHOENIX_*` keys do **not** exist in repo yet.
- Frontend does not translate SELECT option values.
- `SettingsPanel` SUBCATEGORY_LABELS includes `langsmith` but not `phoenix` (add when introducing subcategory).
- Whether to keep `LANGSMITH_TRACING` boolean vs fully replace with SELECT is a product choice; UI mechanics support either, but only object `depends_on` works for multi-provider children.
- Nested depends_on (e.g. child depends on provider **and** enable) is **not** supported — single parent only.

## Recommended UI/settings field design (summary)

1. Add **`TRACING_PROVIDER`**: `SettingType.SELECT`, category `TRACING`, `options: ["none","langsmith","phoenix"]`, default `"none"`, `description: "settingDesc.TRACING_PROVIDER"`, `Settings.TRACING_PROVIDER` on `base.py`.
2. Point all **`LANGSMITH_*` credentials/config** (except obsolete master boolean if removed) at  
   `depends_on: {"key": "TRACING_PROVIDER", "value": "langsmith"}` with `subcategory: "langsmith"`.
3. Future **`PHOENIX_*`** same pattern with `value: "phoenix"`, `subcategory: "phoenix"`.
4. Locales: `settingDesc.TRACING_PROVIDER` + any new keys in en/zh/ja/ko/ru; optional `subcategories.phoenix`.
5. Admin dropdown works with **zero frontend code** if `type=select` + `options` are set (generic path).
6. **Hot-reload gap**: plan either process restart for provider changes, or implement env re-sync + tracer reset modeled on sandbox (`config/service.py` affected set + reset helper). Today runtime admin edits do not re-init tracing.
