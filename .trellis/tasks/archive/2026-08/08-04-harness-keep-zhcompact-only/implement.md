# Implement Plan: 删除 legacy/compact_en harness 模式

> 原则：compact_zh 渲染输出逐字不变。所有 format 占位符（`{available_agents}`、`{work_dir}`、`{team_members_description}` 等）原样保留。

## §A 配置层（kernel/config）

### A1. `src/kernel/config/base.py`
- 删 `HarnessMode` Literal（:30）
- 删 `VALID_HARNESS_MODES`（:31）
- 删 `normalize_harness_mode`（:34-39）
- 删 `Settings.AGENT_HARNESS_MODE` 字段（:92）
- 删 `_normalize_agent_harness_mode` field_validator（:466-469）
- 删 `get_active_harness_mode`（:526-527）
- 验证：`python -c "from src.kernel.config import Settings; Settings()"`

### A2. `src/kernel/config/__init__.py`
- 删 `HarnessMode`、`get_active_harness_mode`、`normalize_harness_mode` 的 import（:7,9,11）与 `__all__` 条目（:31-33）

### A3. `src/kernel/config/constants.py`
- 删 `RESTART_REQUIRED_SETTINGS` 中的 `"AGENT_HARNESS_MODE"` 条目 + 注释（:29-30）

### A4. `src/kernel/config/definitions.py`
- 删 `AGENT_HARNESS_MODE` 整个设置定义（:208-216）

## §B 核心层（agents/core）

### B1. `src/agents/core/harness_prompt_overrides.py`（核心重构）
- 删 `select_harness_text`（:27-29）
- 删 `COMPACT_EN_BEHAVIOR_GUIDE`（:32-40）
- 删 `_EN_WRITE_TODOS_TOOL`（:66-69）、`_EN_TOOLS`（:75-86）、`_EN_FIELDS`（:101-110）
- `catalog_for_mode(mode)` → 替换为模块级 `ZH_CATALOG = HarnessCatalog(...)` 单例，用现有 `_ZH_*` / `COMPACT_ZH_BEHAVIOR_GUIDE` 填充（文本不变）
- `build_short_todo_middleware(mode=None)` → 去掉 mode 参数，删 legacy 分支，恒 `[cls(system_prompt=ZH_CATALOG.write_todos_system, tool_description=ZH_CATALOG.tool_descriptions["write_todos"])]`
- `build_harness_extra_middleware(mode=None)` → 去掉 mode 参数，恒 `(*build_short_todo_middleware(), HarnessLocalizationMiddleware(ZH_CATALOG))`
- 删模块级 `_mode`（:330）及 `TOOL_DESCRIPTION_OVERRIDES` / `SHORT_WRITE_TODOS_TOOL` / `SHORT_WRITE_TODOS_SYSTEM` / `SHORT_TASK_TOOL` / `SHORT_READ_FILE` / `SHORT_EXECUTE` / `build_todo_middleware`（:332-344，零引用死代码）
- 保留：`COMPACT_ZH_BEHAVIOR_GUIDE`、`_ZH_*`、`HarnessCatalog`、`VENDOR_AVAILABLE_AGENTS_HEADING`、`_ShortTodoListMiddleware`/`_short_todo_class`、`_compact_schema`、`localize_tool_for_model`、`_replacements`、`_localize_system`、`HarnessLocalizationMiddleware`

### B2. `src/agents/core/persona.py`
- 删 `_HARNESS_MODE = get_active_harness_mode()`（:32）及 import（:24）
- `DEFAULT_ROLE`（:47-49）恒 zh
- 删 `_legacy_behavior_guide`（:88-98）
- `_BEHAVIOR_GUIDE`（:101-104）恒 `COMPACT_ZH_BEHAVIOR_GUIDE`（从 harness_prompt_overrides import）
- 注册块（:107-128）：删 `if != legacy` 条件，恒为完整 kwargs（base_prompt + tool_description_overrides=ZH_CATALOG.tool_descriptions + extra_middleware=lambda: build_harness_extra_middleware() + excluded_middleware=frozenset({TodoListMiddleware})）
- 日志 `logger.info("[Harness] mode=%s", ...)` → 改静态或删除

### B3. `src/agents/core/subagent_prompts.py`
- 删 `_HARNESS_MODE`（:18）及 import
- 4 个 guide（FILE_WORKSPACE/FILE_REVEAL/SAFETY/TOOL_DISCOVERY :171-190）：内联 zh 文本，删 `_LEGACY_*`/`_COMPACT_EN_*`
- `get_memory_guide`（:216-224）：删 legacy 分支，恒返回 `ZH_CATALOG.memory_guide`
- `SUBAGENT_TASK_GUIDE`（:275-279）：内联 zh，删 `_LEGACY_SUBAGENT_TASK_GUIDE`
- `DEFAULT_SUBAGENT_PROMPT` / `DETAILED_SUBAGENT_PROMPT`（:394-405）：内联 zh，删 `_EN_*`
- `build_role_subagent_section`（:422-466）：5 处 `select_harness_text` 内联 zh 文本
- 保留 `_ZH_HANDOFF` 及 `Current task start time` 契约串

## §C Agent 层

### C1. `src/agents/team_agent/prompt.py`
- 删 `_HARNESS_MODE`（:7）及 `get_active_harness_mode` import
- 删 `_LEGACY_TEAM_ROUTER_SYSTEM_PROMPT`（:9-39）
- `TEAM_ROUTER_SYSTEM_PROMPT`（:41-59）= 内联 zh 文本（保留 `{team_members_description}`/`{team_instructions_section}`/`{default_role}` 占位符）
- 删 `SANDBOX_SYSTEM_PROMPT`/`SANDBOX_RUNTIME_SECTION`（:80-90，**死代码**，nodes.py 用 search_agent 版）及其 `_LEGACY_*`
- `build_team_members_description`（:100,104,107）：`member_label="成员"`、`label="能力"`、`label="指令"`（删三元）
- `build_team_router_system_prompt`（:128）：`heading="## 团队指令"`（删三元）

### C2. `src/agents/team_agent/sop/prompt_section.py`
- import 改为 `from src.kernel.config import settings`（删 HarnessMode/get_active_harness_mode）
- 删 `_LEGACY_SOP_GUIDANCE_SECTION`（:13-32）、`_COMPACT_EN_SOP_GUIDANCE_SECTION`（:35-42）
- `build_sop_guidance_section()`（:53-63）：删 mode 参数与三分支，恒 `return _COMPACT_ZH_SOP_GUIDANCE_SECTION.format(max_steps=..., min_steps=...)`

### C3. `src/agents/search_agent/prompt.py`
- 删 `_LEGACY_*`（:12-38）
- `SANDBOX_SYSTEM_PROMPT`/`SANDBOX_RUNTIME_SECTION`/`DEFAULT_SYSTEM_PROMPT`（:41,49,59）= 内联 zh（保留 `transfer_file`/`transfer_path`/`upload_url_to_sandbox(url, absolute_file_path)`/`{work_dir}` 契约串）
- 删死常量 `DEFERRED_TOOL_GUIDE`（:67）

### C4. `src/agents/fast_agent/prompt.py`
- 删 `_LEGACY_FAST_SYSTEM_PROMPT`（:10-19）
- `FAST_SYSTEM_PROMPT`（:21-32）= 内联 zh（保留 `memory_retain`/`memory_recall`/`memory_delete`/`<memory_index>` 契约串）
- 删死常量 `DEFERRED_TOOL_GUIDE`（:33）

### C5. `src/infra/tool/deferred_manager.py`
- 删 `_HARNESS_MODE`（:20）及 import
- `DEFERRED_TOOL_SEARCH_GUIDE`（:23-48）：恒 zh 分支
- hidden-count 文案（:235-257）：恒 zh 分支

## §D 前端

### D1. `frontend/src/i18n/locales/en.json`
- 删 `settingDesc.AGENT_HARNESS_MODE`（:2169）
- 删 `subcategories.harness`（:2615）

### D2. `frontend/src/i18n/locales/zh.json`
- 同上两行

### D3. `frontend/src/components/panels/SettingsPanel.tsx`
- 删 `SUBCATEGORY_LABELS` 中 `harness: t("subcategories.harness")`（:180）

## §E 测试（5 个文件）

### E1. `tests/kernel/config/test_sandbox_image_description_setting.py`
- 删 `test_agent_harness_mode_moved_to_agent_category`（:30-39）

### E2. `tests/agents/core/test_harness_prompt_overrides.py`（主力大改）
- `test_mode_validation_and_catalogs`（:47-58）：删 normalize 断言；循环收敛为单 zh
- `test_mode_setting_is_typed_visible_and_restart_required`（:59-77）：**删除整测试**
- 所有 `catalog_for_mode("compact_zh")` → `ZH_CATALOG`
- 所有 `build_short_todo_middleware("compact_zh")` → `build_short_todo_middleware()`
- 所有 `build_harness_extra_middleware("compact_zh")` → `build_harness_extra_middleware()`
- `test_extra_middleware_*`（:250-255）：删 `build_harness_extra_middleware("legacy") == ()` 断言
- `test_three_modes_boot_in_isolated_processes`（:319-349）：重写为单 zh 冒烟（删三模式遍历与 env 注入）
- `test_deferred_manager_*`（:351-365）：删 `_HARNESS_MODE` 断言与 env 注入，保留 import 无循环验证
- 保留：vendor SHA-256 快照、schema 相等性、cache extras、middleware 顺序断言

### E3. `tests/agents/core/test_subagent_prompts.py`
- `_prompt_contract_for_mode`（:162-194）：去 mode 参数，删 env 注入
- `test_each_mode_preserves_critical_prompt_contracts`（:196）：删 parametrize，固定 zh
- `test_legacy_prompt_rollback_fixture_is_pinned`（:232-244）：**删除整测试**

### E4. `tests/agents/test_sop_prompt_section.py`
- `test_legacy_section_contains_key_constraints`（:12-22）：**删除整测试**
- `test_compact_zh_section_contains_key_constraints`（:23-32）：`build_sop_guidance_section("compact_zh")` → `build_sop_guidance_section()`
- `test_default_mode_follows_active_harness_mode`（:36-42）：重写——删 monkeypatch，直接断言 zh 文案

### E5. `tests/unit/agents/test_team_router.py`（修复 drift）
- `:60` `"Capability summary: ..."` → `"能力: Investigates sources and verifies claims."`
- `:86` `"## Team Instructions"` → `"## 团队指令"`
- `:110` `"Do not dispatch onboarding, coordination, reminder, or notification messages"` → 对照 zh TEAM_ROUTER_SYSTEM_PROMPT 改中文等价断言
- `:111` `"The \`task\` tool is for work assignments only"` → 对照 zh 改中文等价断言

## §F 文档

### F1. `.trellis/spec/backend/agent-harness.md`
- 标题去 "Mode"
- §2 签名：删 HarnessMode/normalize/get_active 签名
- §3 契约：删 AGENT_HARNESS_MODE startup-only 条
- §4 错误矩阵：删 Unknown mode / whitespace 两行
- §5 Good/Base/Bad：删 legacy 基线
- §6 测试：三模式 → 单 zh 契约；删 legacy rollback hash

### F2. `.trellis/spec/backend/index.md`
- `:34` 索引项更新为无模式描述

## §G 验证

1. `ruff check src/ tests/`
2. `mypy src/`
3. `python -m pytest tests/agents/core/test_harness_prompt_overrides.py tests/agents/core/test_subagent_prompts.py tests/agents/test_sop_prompt_section.py tests/unit/agents/test_team_router.py tests/kernel/config/test_sandbox_image_description_setting.py -v`
4. 残留扫描：grep 全仓 `select_harness_text|catalog_for_mode|get_active_harness_mode|normalize_harness_mode|HarnessMode|AGENT_HARNESS_MODE` → 仅剩历史注释/不相关领域
5. 前端：`pnpm -C frontend build`（或 typecheck）确认无悬空 key

## 实施顺序

```
A (config) → B (core) → C (agents) → D (frontend) → E (tests) → F (docs) → G (verify)
```

A 必须先做（删 HarnessMode 后 B/C 的 import 会断，正好暴露所有消费点）。B/C 可由实现子智能体一次性完成。D/E/F 可在 B/C 后跟进。
