# Research: frontend / i18n / tests / docs — harness 模式引用清册

- **Query**: 摸清 frontend 设置 UI、i18n、测试面、文档面中所有与 harness 模式（legacy/compact_en/compact_zh 开关）相关的引用，供"删除 legacy/compact_en、只保留 compact_zh"任务使用
- **Scope**: mixed（internal 为主）
- **Date**: 2026-08-04
- **Task**: `.trellis/tasks/08-04-harness-keep-zhcompact-only`

## 结论速览

前端不存在硬编码的 harness 模式选择控件 —— `AGENT_HARNESS_MODE` 是**后端 `SETTING_DEFINITIONS` 驱动、前端泛型渲染**的 SELECT 设置。删除后端定义后，设置项自动从设置 API 响应消失，前端 UI 自动不渲染。前端需要手工删除的只有 **2 个 i18n key（en/zh 各 2 条）** 和 **SettingsPanel.tsx 的 1 行 subcategory label**。ja/ko/ru 三个 locale 本就没有这些 key。

---

## ① frontend 设置 UI 清册

### 渲染机制（无硬编码 harness 控件）

| 文件:行号 | 内容 | 删除影响 |
|---|---|---|
| `frontend/src/components/panels/SettingsPanel.tsx:180` | `SUBCATEGORY_LABELS` 中 `harness: t("subcategories.harness")` | 删除该行（唯一前端硬编码 harness 引用） |
| `frontend/src/components/panels/SettingsPanel.tsx:301` | `label: key ? SUBCATEGORY_LABELS[key] \|\| key : ""` | 不改；label 缺失时回落为原始 key |
| `frontend/src/components/panels/SettingsPanel.tsx:704-716` | `isSelect = ... \|\| (setting.type === "select" && setting.options)` | 不改；泛型 SELECT 判定 |
| `frontend/src/components/panels/SettingsPanel.tsx:904-906` | 选项渲染 `setting.options?.map((opt) => ({ value: opt, label: opt }))` | 不改；选项 label 直接显示原值（**无选项 i18n key**） |
| `frontend/src/components/panels/SettingsPanel.tsx:264` | 搜索匹配 `t(setting.description)` | 不改；description 是 `settingDesc.X` key，经 i18n 解析 |

### 设置数据流（无需改动）

- `frontend/src/hooks/useSettings.ts` — `settingsApi.list()` 拉取设置；`updateSetting(key, value)` 保存；无 harness 特判。
- `frontend/src/contexts/SettingsContext.tsx` — 透传 `useSettings()` 结果；无 harness 特判。
- `frontend/src/services/api/` — 泛型 settings API 封装；无 harness 端点。
- `frontend/src/types/settings.ts` — `SettingItem` 泛型结构；无 harness 字段。
- `frontend/src/components/panels/SettingsPanel.constants.ts` — grep `harness` 无匹配（MODEL_CONFIG_SETTING_KEYS、CATEGORY_ORDER 等均不含 harness）。

**关键点**：后端删除 `AGENT_HARNESS_MODE` 定义后，`SettingsService.get_all`（`src/infra/settings/storage.py:61-64` 按 `SETTING_DEFINITIONS` 迭代）不再返回该项，前端 `filteredSettings` / `groupedSettings` 自动不含它，"harness" 子分类自动消失（组为空则不渲染）。前端零运行时改动。

---

## ② i18n key 清册（5 个 locale 全部穷尽）

grep `AGENT_HARNESS_MODE|harness|compact`（含大小写不敏感）覆盖 `frontend/src/i18n/locales/` 全部 5 个文件，仅命中以下 4 条：

| locale | 行号 | key | 值 | 处理 |
|---|---|---|---|---|
| `en.json` | 2169 | `settingDesc.AGENT_HARNESS_MODE` | "Agent harness language/compression mode under Agent settings (restart required)" | **删除** |
| `en.json` | 2615 | `subcategories.harness` | "Harness" | **删除** |
| `zh.json` | 2169 | `settingDesc.AGENT_HARNESS_MODE` | "代理设置中的 Harness 语言/压缩模式（需重启）" | **删除** |
| `zh.json` | 2615 | `subcategories.harness` | "Harness" | **删除** |
| `ja.json` / `ko.json` / `ru.json` | — | 无 | 无（三个 locale 的 `settingDesc` 块 ~1927 行起、`subcategories` 块 ~2444 行起均无 harness key） | 无需改动 |

- **选项标签 i18n**：不存在。选项 `legacy/compact_en/compact_zh` 由 `SettingsPanel.tsx:906` 直接 `label: opt` 原样显示，无 `options.xxx` 类 key。
- **缺失 key 的 fallback**：`frontend/src/i18n/index.ts:44` `fallbackLng: "en"`；且 `SettingsPanel.tsx:301` 有 `SUBCATEGORY_LABELS[key] || key` 兜底。删除后无悬空引用风险（设置项与 label 同步消失）。

---

## ③ 测试面清册

### A. 必须直接改动的测试文件（4 个）

#### 1. `tests/kernel/config/test_sandbox_image_description_setting.py`
- 行 30-38 `test_agent_harness_mode_moved_to_agent_category`：断言 `SETTING_DEFINITIONS["AGENT_HARNESS_MODE"]` 的 category/subcategory/type/default/options 及 `RESTART_REQUIRED_SETTINGS` 包含。
- **改动方式**：整个测试函数**删除**（定义不存在后无物可断言）。文件其余两个测试（SANDBOX_IMAGE_DESCRIPTION）保留。

#### 2. `tests/agents/core/test_harness_prompt_overrides.py`（主力测试，大改）
| 行号 | 测试 | 改动方式 |
|---|---|---|
| 47-58 | `test_mode_validation_and_catalogs` | 删 `normalize_harness_mode` 断言（46-49 行）；`for mode in ("compact_en", "compact_zh")` 循环改为**单 mode `compact_zh`**（51 行），保留长度断言 |
| 59-77 | `test_mode_setting_is_typed_visible_and_restart_required` | 整个测试**删除**（依赖 AGENT_HARNESS_MODE 定义/校验/RESTART_REQUIRED） |
| 79-85 | `test_compact_zh_uses_chinese_human_guidance` | `catalog_for_mode("compact_zh")` → 改为无 mode 的新接口（如 `get_compact_zh_catalog()` 或固化常量）；断言不变 |
| 87-107 | `test_todo_replacement_survives_exact_type_exclusion` | 91 行 `build_short_todo_middleware("compact_zh")` 去掉 mode 参数；94-99 行保留 |
| 109-133 | `test_model_view_preserves_middleware_rendered_tool_descriptions` | `catalog_for_mode("compact_zh")` → 新无参接口 |
| 135-158 | `test_vendor_system_prompt_snapshots_are_pinned` | 保留（vendor SHA 不变）；`VENDOR_AVAILABLE_AGENTS_HEADING` 断言保留 |
| 160-176 | `test_schema_localization_preserves_machine_contract` | 同上，仅 catalog 来源改无参 |
| 178-188 | `test_search_tools_schema_and_description_are_localized` | 同上 |
| 190-206 | `test_schema_localization_preserves_cache_extras_and_unknown_tools` | 同上 |
| 208-226 | `test_original_pydantic_schemas_still_enforce_required_defaults_and_enums` | 不涉 mode，保留 |
| 228-248 | `test_compact_tools_are_smaller_than_native_vendor_schemas` | 227 行 `catalog_for_mode("compact_zh")` 改无参；`legacy_payload` 变量名建议改 `native_payload`（纯命名，非必需） |
| 250-255 | `test_extra_middleware_contains_todo_and_localizer` | 251 行 `build_harness_extra_middleware("compact_zh")` 去参；**255 行 `build_harness_extra_middleware("legacy") == ()` 断言删除** |
| 257-296 | `test_localizer_rewrites_final_model_request_without_replacing_runtime_tools` | `HarnessLocalizationMiddleware(catalog_for_mode("compact_zh"))` → 无参 catalog；其余断言不变 |
| 299-317 | `test_shared_profile_resolves_for_supported_adapters` | 保留（deepagents 注册路径保留 compact_zh 行为）；确认 profile 断言不依赖 mode 参数 |
| 319-349 | `test_three_modes_boot_in_isolated_processes` | **整个测试删除或重写**为单进程 `compact_zh` 冒烟（不再遍历 3 mode、不再设 `AGENT_HARNESS_MODE` 环境变量、删除 legacy/compact_en 行为断言 345-349 行） |
| 351-365 | `test_deferred_manager_can_be_imported_first_without_cycle` | `dm._HARNESS_MODE` 与 `AGENT_HARNESS_MODE=compact_zh` 环境变量相关——若 `deferred_manager._HARNESS_MODE` 一并删除，测试改为只验 import 无循环（去掉 print 断言）；若保留模块级常量则仅去 env 变量 |

#### 3. `tests/agents/core/test_subagent_prompts.py`
- 行 162-198 `_prompt_contract_for_mode(mode)`：删 `env["AGENT_HARNESS_MODE"] = mode`（184 行）；改为无参。
- 行 196-229 `test_each_mode_preserves_critical_prompt_contracts`（`@pytest.mark.parametrize("mode", ["legacy", "compact_en", "compact_zh"])`，196 行）：**参数化删除，固定 compact_zh 单 mode**；222-229 行的 `if mode == "compact_zh": ... else: ...` 中文分支保留、英文分支删除。
- 行 232-244 `test_legacy_prompt_rollback_fixture_is_pinned`：**整个测试删除**（legacy 回退 fixture 不存在）。
- 文件头注释（1 行）"direct module assertions require default compact_zh" 更新。

#### 4. `tests/agents/test_sop_prompt_section.py`
- 行 12-22 `test_legacy_section_contains_key_constraints`：**整个测试删除**（`build_sop_guidance_section("legacy")` 分支不存在）。
- 行 23-32 `test_compact_zh_section_contains_key_constraints`：`build_sop_guidance_section("compact_zh")` → 去参；断言不变。
- 行 36-42 `test_default_mode_follows_active_harness_mode`：monkeypatch `get_active_harness_mode`（39 行）——`get_active_harness_mode` 删除后**重写/删除**（若 `build_sop_guidance_section()` 默认即 zh，可改为直接断言 zh 文案；monkeypatch 逻辑删除）。
- 文件头注释（1 行）更新。

### B. 保留（grep 命中的非模式引用）

| 文件 | 命中 | 判定 |
|---|---|---|
| `tests/agents/test_team_agent_sop_tool_hook.py:83-89` | monkeypatch `deepagents.HarnessProfile` / `register_harness_profile` | **保留**；deepagents 注册路径继续存在（compact_zh 固化），仅当实现删除 register 调用时才需复查 |
| `tests/agents/test_team_agent_sandbox_support.py:84-90` | 同上 | 同上 |
| `tests/agents/test_sop_tool_gate.py:84` 等 | `class _GateHarness` | **保留**；本地测试桩类名，与 harness 模式无关 |

### C. frontend 测试

- **无**。`frontend/src/components/panels/__tests__/`（4 个文件：jsonSchemaEditorLayout / sessionHelpers / sessionSidebarSafeArea / usersPanelFormLayout）、`frontend/src/i18n/__tests__/`（forkMessageKeys / roleMaxChannelsKeys）、`frontend/src/hooks/__tests__/`（10 个）grep `harness|AGENT_HARNESS_MODE` 均无命中。SettingsPanel 无快照/渲染测试。

### D. prompt cache / persona 测试

- `tests/` 全仓 grep `harness`（大小写不敏感，87 处命中）与 `AGENT_HARNESS_MODE|HarnessMode|compact_en|compact_zh|select_harness_text|catalog_for_mode|get_active_harness_mode|normalize_harness_mode` 交叉核对：**无独立 prompt cache harness 测试文件**；persona harness 断言集中在 `test_harness_prompt_overrides.py`（上述 A2）。

---

## ④ 文档面清单

### `.trellis/spec/backend/agent-harness.md`（需大改/重写）
| 行号 | 段落 | 更新要点 |
|---|---|---|
| 1 | 标题 "Agent Harness Mode and Localization" | 改标题：去 "Mode"，如 "Agent Harness Localization (compact_zh)" |
| 3-8 | §1 Scope/Trigger | 删 `AGENT_HARNESS_MODE` 触发条件 |
| 10-18 | §2 Signatures | `HarnessMode` / `normalize_harness_mode` / `get_active_harness_mode` 签名**删除**；`localize_tool_for_model(tool, catalog)` 改无 mode 形态 |
| 20-22 | "Shared mode helpers ..." 段 | 删（mode 机制不存在，import 边界理由消失，但"infra 不得 import agents"约束本身可保留为一般约束） |
| 24-28 | §3 Contracts 第 1 条 | `AGENT_HARNESS_MODE` startup-only/restart 契约**整条删除** |
| 38-39 | "Vendor prompt replacements are pinned by SHA-256" | 保留（vendor 快照测试仍在） |
| 40-50 | §4 Validation & Error Matrix | 删 "Unknown mode"/"Mode has surrounding whitespace/case" 两行；其余保留 |
| 52-58 | §5 Good/Base/Bad | "Base: legacy ..." 删除或改写为"删除前历史"；"Good: compact_zh ..." 保留 |
| 62-70 | §6 Tests Required | "Isolated-process tests for all three modes" → "compact_zh 单模式契约测试"；"Vendor prompt and legacy rollback hashes" → 删 legacy rollback |
| 72-81 | §7 Wrong vs Correct | 保留（不涉 mode） |

### `.trellis/spec/backend/index.md`
- 行 34：索引项 "Agent Harness Mode & Localization — Reversible harness modes, ..." → 更新为无模式描述（如 "compact_zh harness localization"）。

### 其他 spec（grep `harness|compact_en|compact_zh|AGENT_HARNESS_MODE` 全仓核验）
- `.trellis/spec/frontend/index.md:21,24`、`oa-sso-entry.md`、`quality-guidelines.md`、`backend/analytics-persona-and-lists.md:52`、`auth-idle-sessions.md:77`、`wecom-network-settings.md:111`、`wecom-persona-connection-status.md:136` 的 "legacy" 均指其他领域（pdf.js、token 别名、预设、网络配置明文），**与 harness 无关，不动**。

### 仓库根目录 / scripts / docs
- `README.md`、`docs/wecom-intranet-deployment.md`、`scripts/`：grep `harness|HARNESS` **无命中**。
- `CHANGELOG.md`：grep `harness` 无命中（历史上未记录该设置项，无需回改）。
- **`.env`（本地，非 .env.example）行 243：`AGENT_HARNESS_MODE=compact_zh`** —— 建议顺手删除（`.env.example` 无此项，gitignored，不影响 CI）。

### 归档任务（历史依据，仅参考不复制）
- `.trellis/tasks/archive/2026-07/07-20-compress-agent-harness-prompts/`（prd/design/implement/result）：
  - prd.md：压缩 vendor 工具 schema + 首方 system prompt，引入 `legacy|compact_en|compact_zh` 三模式（第 90 行决策），默认 compact_zh。
  - result.md：模式测量表（legacy 29,334 chars / compact_en 10,274 / compact_zh 4,428）；"Rollback" 节即本任务要删除的回退路径；记录 `[Harness] mode=<mode>` 启动日志（`src/agents/core/persona.py:127`）。
  - 删除任务验收时须确认：compact_zh 渲染逐字一致（保留 `test_harness_prompt_overrides.py` 中 SHA-256 vendor 快照与 schema 相等性测试作为回归基线）。

---

## ⑤ 后端设置定义补充（供后端子智能体）

| 文件:行号 | 内容 | 处理 |
|---|---|---|
| `src/kernel/config/definitions.py:208-215` | `AGENT_HARNESS_MODE`：SELECT / category=AGENT / **subcategory="harness"（全仓唯一，grep `"harness"` 于 src/ 仅此 1 处）** / default=compact_zh / options=[legacy,compact_en,compact_zh] | 删除定义；**subcategory "harness" 无其他设置项 → 分类可整体删除**（对应前端 SUBCATEGORY_LABELS 行 180 与 i18n key 一并删） |
| `src/kernel/config/constants.py:30` | `RESTART_REQUIRED_SETTINGS` 含 `AGENT_HARNESS_MODE`（注释 "Harness profiles register at import time."，行 29） | 删除该条目与注释 |
| `src/kernel/config/base.py:31-39` | `HarnessMode` Literal / `VALID_HARNESS_MODES` / `normalize_harness_mode` | 删除 |
| `src/kernel/config/base.py:92` | `AGENT_HARNESS_MODE: HarnessMode = "compact_zh"` 字段 | 删除 |
| `src/kernel/config/base.py:466-469` | `@field_validator("AGENT_HARNESS_MODE", mode="before")` `_normalize_agent_harness_mode` | 删除 |
| `src/kernel/config/base.py:526-527` | `get_active_harness_mode()` | 删除 |
| `src/kernel/config/__init__.py:9,11,32-33` | 导出 `get_active_harness_mode` / `normalize_harness_mode` | 删除导出 |
| `src/api/routes/settings.py` | **无 harness 特定端点**（仅泛型 GET `/`、wecom-network 等） | 无改动 |
| `src/infra/settings/storage.py:61-64` | `get_all` 按 `SETTING_DEFINITIONS` 迭代 + `frontend_visible` 过滤 | 无改动（定义删除即自动消失）。注：`AGENT_HARNESS_MODE` 定义无 `frontend_visible`，普通用户本就不可见，仅 admin 可见 |
| `src/agents/core/harness_prompt_overrides.py` | `select_harness_text`(27) / `COMPACT_EN_BEHAVIOR_GUIDE`(32) / `_EN_WRITE_TODOS_TOOL`(66) / `_EN_TOOLS`(75) / `_EN_FIELDS`(101) / `catalog_for_mode(mode)`(128) / `build_short_todo_middleware(mode)`(197) / `localize_tool_for_model(tool, catalog)`(227) / `build_harness_extra_middleware(mode)`(322) | 去 mode 参数与英文 catalog；固化 zh（详见 PRD 需求 2-3） |
| `src/agents/core/persona.py:32,104,116,127` | `_HARNESS_MODE = get_active_harness_mode()` + `catalog_for_mode(_HARNESS_MODE)` + `logger.info("[Harness] mode=%s")` | 删 mode 分支、固化 zh；日志删或改静态 |
| `src/agents/core/subagent_prompts.py:18,219,223` | `_HARNESS_MODE` + legacy 分支（219 行） | 删 legacy 分支，固化 zh |
| `src/agents/team_agent/prompt.py:5,7,41,81,86,100,104,107,128` | `_HARNESS_MODE` + `select_harness_text` + member_label/能力/指令/## 团队指令 条件分支 | 固化 zh 分支 |
| `src/agents/fast_agent/prompt.py:8,21` | `select_harness_text` | 固化 zh |
| `src/agents/search_agent/prompt.py:10,41,49,59` | `select_harness_text` | 固化 zh |
| `src/agents/team_agent/sop/prompt_section.py:4` | 模块 docstring 提及三模式 | 更新注释 |
| `src/infra/tool/deferred_manager.py` | `_HARNESS_MODE`（test 357 行引用 `dm._HARNESS_MODE`） | 删除/固化 |

---

## ⑥ 潜在问题清单

1. **前端读取未知 key**：不会报错。后端定义删除 → `get_settings` 响应不含该 key → 前端泛型渲染零改动。反向顺序（先删前端 label/key、后删后端）也无碍：`SUBCATEGORY_LABELS[key] || key` 兜底显示原始 key。**无顺序风险**。
2. **i18n 悬空 key**：删除设置项后，`settingDesc.AGENT_HARNESS_MODE`（en/zh:2169）与 `subcategories.harness`（en/zh:2615）必须**同步删除**，否则成为死 key（不报错但污染）。ja/ko/ru 无此 key，无需动。
3. **`@pytest.mark.parametrize` 跨三 mode**：`tests/agents/core/test_subagent_prompts.py:196` 是唯一跨三 mode 参数化；`test_harness_prompt_overrides.py:51` 是双 mode 循环（compact_en+compact_zh）；两处都需收敛为单 mode。另有 4 处子进程枚举三 mode（`test_harness_prompt_overrides.py:332` 循环、`test_subagent_prompts.py:184` env 注入）。
4. **`build_sop_guidance_section` 默认参数**：`tests/agents/test_sop_prompt_section.py:39` monkeypatch `get_active_harness_mode` 依赖该函数存在；函数删除后此测试必挂，需重写。
5. **`deferred_manager._HARNESS_MODE`**：`test_harness_prompt_overrides.py:357` 通过子进程 print 断言其值；删除模块级常量时该测试尾部断言要同步删。
6. **`.env` 残留**：本地 `.env:243` 有 `AGENT_HARNESS_MODE=compact_zh`；若 pydantic-settings 仍读 env 而代码已删字段，`AGENT_HARNESS_MODE` 作为未知 env 变量被 pydantic 忽略（`extra` 默认忽略）——不报错，但建议清理。
7. **验收标准冲突点**：PRD 验收要求 `src/`、`tests/`、`frontend/` 无 `legacy`/`compact_en`/`HarnessMode`/`AGENT_HARNESS_MODE` 引用（纯历史注释除外）。`tests/agents/test_harness_prompt_overrides.py:308-309` 的模型名 `claude-harness-test`/`gpt-harness-test` 是字符串 fixture，不在禁用词表内，可保留。
8. **harness 子分类清空**：后端 definitions.py 中 subcategory="harness" 仅 AGENT_HARNESS_MODE 一项 → 分类删除后前端 label（SettingsPanel.tsx:180）与 i18n key（en/zh:2615）一并删除，无孤立 UI。
9. **vendor SHA 快照**：`test_harness_prompt_overrides.py:135-158` 的 vendor 字符串 SHA-256 依赖 deepagents 版本，与本次删除无关，保留即守住"compact_zh 逐字一致"验收。
10. **deepagents shim 测试**：`test_team_agent_sop_tool_hook.py` / `test_team_agent_sandbox_support.py` 用 `raising=False` monkeypatch `HarnessProfile`/`register_harness_profile`；若实现删除 register 调用，这两处 shim 可能变为无害冗余（可留可清），但**测试本身不必删**。

---

## ⑦ 删除建议（操作顺序）

1. **后端先行**（后端子智能体）：删 definitions.py:208-215、constants.py:29-30、base.py:31-39/92/466-469/526-527、`__init__.py` 导出；固化 harness_prompt_overrides / persona / subagent_prompts / team_agent / fast_agent / search_agent / sop / deferred_manager。
2. **前端 i18n**（与后端并行）：删 en.json:2169、2615；zh.json:2169、2615；删 SettingsPanel.tsx:180。
3. **测试更新**（后端实现后）：按 ③A 清单改 4 个文件（删 4 个整测试函数 + 收敛参数化 + 去 mode 参数）。
4. **文档**：重写 `.trellis/spec/backend/agent-harness.md`，更新 `.trellis/spec/backend/index.md:34`。
5. **清理**：本地 `.env:243` 移除；归档 result.md 的 rollback 章节在删除后仅存历史语义。
6. **回归验证**：保留 vendor SHA 快照 + schema 相等性 + `test_compact_tools_are_smaller_than_native_vendor_schemas` 的尺寸断言，作为 compact_zh 逐字不变的看门狗。
