# Design: 删除 legacy/compact_en harness 模式，只保留 compact_zh

## 决策记录（用户确认）

| 决策点 | 选择 |
|--------|------|
| 删除范围 | 彻底删除 `AGENT_HARNESS_MODE` 设置与模式机制，hardcode compact_zh |
| 常量命名 | 保留 `_ZH` 后缀（surgical，最小 diff） |
| team_agent/prompt.py 死代码 | 删除 SANDBOX_SYSTEM_PROMPT / SANDBOX_RUNTIME_SECTION（零引用，nodes.py 用 search_agent 版） |
| 测试验证 | 保留+改造现有快照测试（vendor SHA-256 + schema 相等性），三模式子进程测试改为单 zh 冒烟 |
| test_team_router.py 英文断言 | 改为中文断言（修复 compact_zh 默认下的 drift） |

## 架构（不变量）

删除模式开关后，以下必须**逐字不变**：

- compact_zh 所有 prompt 文本、工具描述、schema 字段描述
- 工具名、schema 属性名、required/enum/default、类型
- 机器契约标识：`Current task start time`、`{available_agents}`、`{work_dir}`、`transfer_file`/`transfer_path`/`upload_url_to_sandbox(url, absolute_file_path)`
- middleware 顺序：`(*ShortTodoListMiddleware, HarnessLocalizationMiddleware)`
- 工具顺序与 prompt cache 块结构
- deepagents `HarnessProfile` 注册路径（base_prompt + tool_description_overrides + extra_middleware + excluded_middleware，三 provider key）
- vendor system prompt SHA-256 快照

## 删除边界

```
配置层 (kernel/config)
  ├─ base.py: HarnessMode / VALID_HARNESS_MODES / normalize_harness_mode / AGENT_HARNESS_MODE 字段 / validator / get_active_harness_mode
  ├─ __init__.py: 3 个导出
  ├─ constants.py: RESTART_REQUIRED_SETTINGS 条目
  └─ definitions.py: AGENT_HARNESS_MODE 定义（subcategory "harness" 唯一项 → 分类整体消失）

核心层 (agents/core)
  ├─ harness_prompt_overrides.py: select_harness_text / COMPACT_EN_* / _EN_* / catalog_for_mode mode 参数 / 7 个死常量 / build_*_middleware mode 参数
  ├─ persona.py: _HARNESS_MODE / _legacy_behavior_guide / 注册块 legacy 分支
  └─ subagent_prompts.py: _HARNESS_MODE / legacy 分支 / 4 guide + SUBAGENT_PROMPT 的 EN/legacy 文本

Agent 层
  ├─ team_agent/prompt.py: _HARNESS_MODE / 3 select_harness_text / 4 处三元条件 / 死代码 SANDBOX_*
  ├─ team_agent/sop/prompt_section.py: _LEGACY / _COMPACT_EN / mode 参数
  ├─ search_agent/prompt.py: 3 select_harness_text / 死 DEFERRED_TOOL_GUIDE
  ├─ fast_agent/prompt.py: 1 select_harness_text / 死 DEFERRED_TOOL_GUIDE
  └─ infra/tool/deferred_manager.py: _HARNESS_MODE / 3 处分支

前端
  ├─ i18n en/zh.json: settingDesc.AGENT_HARNESS_MODE + subcategories.harness（各 2 key）
  └─ SettingsPanel.tsx:180: SUBCATEGORY_LABELS.harness

测试 (5 个文件) — 见 implement.md §E
文档 (2 个文件) — 见 implement.md §F
```

## harness_prompt_overrides.py 重构方案

保留的机制（去掉 mode 分支，恒走 zh）：

| 函数/类 | 改法 |
|---------|------|
| `select_harness_text` | **删除**；调用方内联 zh 常量 |
| `catalog_for_mode(mode)` | 改为 `ZH_CATALOG = HarnessCatalog(...)` 模块级单例（去掉 mode 参数与分支） |
| `COMPACT_EN_BEHAVIOR_GUIDE` | 删除 |
| `COMPACT_ZH_BEHAVIOR_GUIDE` | 保留（文本不变） |
| `_EN_WRITE_TODOS_TOOL` / `_EN_TOOLS` / `_EN_FIELDS` | 删除 |
| `_ZH_*` | 保留（文本不变） |
| `VENDOR_AVAILABLE_AGENTS_HEADING` | 保留（middleware `_replacements` 仍需它做 vendor 串匹配） |
| `build_short_todo_middleware(mode)` | 去掉 mode 参数，恒返回 zh `[cls(...)]` |
| `build_harness_extra_middleware(mode)` | 去掉 mode 参数，恒返回 `(*build_short_todo_middleware(), HarnessLocalizationMiddleware(ZH_CATALOG))` |
| `HarnessLocalizationMiddleware` | 保留（构造参数恒传 ZH_CATALOG） |
| `localize_tool_for_model` / `_compact_schema` / `_replacements` / `_localize_system` | 保留（机制不变，catalog 恒为 zh） |
| 模块级 `_mode` / `TOOL_DESCRIPTION_OVERRIDES` / `SHORT_*` / `build_todo_middleware` (330-344) | **删除**（零引用死代码） |

## persona.py 注册块改法

```python
# 删除前
_HARNESS_MODE = get_active_harness_mode()
_BEHAVIOR_GUIDE = _legacy_behavior_guide() if _HARNESS_MODE == "legacy" else catalog_for_mode(_HARNESS_MODE).behavior_guide
# 注册: if != legacy 才加 overrides/middleware/excluded

# 删除后（hardcode zh）
_BEHAVIOR_GUIDE = COMPACT_ZH_BEHAVIOR_GUIDE  # 直接引用
# 注册: 恒为完整 kwargs（base_prompt + tool_description_overrides + extra_middleware + excluded_middleware）
# extra_middleware=lambda: build_harness_extra_middleware()  （无参）
# logger.info("[Harness] mode=compact_zh") 或删除日志
```

`DEFAULT_ROLE` 恒 `"你是具备工具和技能的智能助手。"`；`_legacy_behavior_guide` 删除。

## 潜在风险与缓解

| 风险 | 缓解 |
|------|------|
| hardcode 重构改动 middleware 返回顺序 | 保留 `test_extra_middleware_contains_todo_and_localizer` 断言顺序 |
| zh 文本意外改动 | 保留 vendor SHA-256 快照 + schema 相等性测试 |
| format 占位符丢失（`{available_agents}`、`{work_dir}`、`{team_members_description}` 等） | 内联 zh 文本时逐字保留占位符；现有测试覆盖 task `{available_agents}` |
| test_team_router.py 改中文断言遗漏 | 逐条对照 zh prompt 文本 |
| deferred_manager 分支遗漏 | 3 处分支逐一固化 |

## 扩展范围：剩余英文翻译为浓缩中文

除删除 legacy/compact_en 外，用户要求把 harness 表面**所有剩余英文**也改成浓缩中文，建成全中文 harness。

### 工具描述翻译（加入 catalog `_ZH_TOOLS` + `_ZH_FIELDS`）

`localize_tool_for_model` 按 `tool.name in catalog.schema_fields` 覆盖模型视图。需新增以下工具到 catalog（工具名/参数名/枚举值是机器契约，不译）：

| 工具 | 参数 | 优先级 |
|------|------|--------|
| `memory_retain` | content/title/summary/context/tags/existing_memory_id | P1 |
| `memory_recall` | query/max_results/memory_types | P1 |
| `memory_delete` | memory_id | P1 |
| `read_document` | url | P4 |
| `dify_kb_retrieve` | query/top_k/score_threshold | P4 |
| `audio_transcribe` | url/model/language/prompt | P4 |
| `upload_url_to_sandbox` | url/file_path | P5（字段已中文，仅 docstring） |
| `find_skills` | query/tags | P5 |
| `install_skill` | name | P5 |
| `env_var_list` | — | P5 |
| `env_var_set` | name/value/description | P5 |
| `env_var_delete` | name | P5 |
| `env_var_delete_all` | — | P5 |
| `sandbox_mcp_add` | server_name/command/env_keys | P5 |
| `sandbox_mcp_update` | server_name/command/env_keys | P5 |
| `sandbox_mcp_remove` | server_name | P5 |
| `image_generate` | prompt/input_images/background/… | P5 |
| `create_persona_preset` | name/system_prompt/description/avatar/tags/… | P7 |
| `update_persona_preset` | preset_id/… | P7 |
| `search_persona_presets` | query/tag | P7 |
| `create_agent_team` | name/members/… | P7 |

### 系统 prompt 段落翻译

| # | 文件 | 段落 | 优先级 |
|---|------|------|--------|
| 1 | `src/infra/skill/loader.py` | `build_skills_prompt` `## Skills System` | P2 |
| 2 | `src/infra/persona_preset/skill_harness.py` | `## Persona skill capabilities` | P6 |
| 3 | `src/infra/tool/skill_marketplace_tool.py` | `_MARKETPLACE_SKILL_PROMPT` | P6 |
| 4 | `src/infra/goal.py` | `## Active Goal` | P9 |
| 5 | `src/infra/tool/env_var_prompt.py` | `## Available Environment Variables` | P8 |
| 6 | `src/infra/tool/sandbox_mcp_prompt.py` | `## Sandbox Tools (NOT MCP…)` + overflow | P8 |
| 7 | `fast_agent/nodes.py` + `search_agent/nodes.py` | general-purpose subagent description | P3 |
| 8 | `team_agent/nodes.py` | team member subagent description 模板 | P3 |

### 不翻译（机器契约标识）

工具名、参数名、枚举值、`Current task start time`、`## Handoff Notes` + 10 个交接字段标签、`## MCP Tools (Deferred)`（联动 prompt_caching.py volatile 前缀判定）、`<memory_index>`、`/skills/`、`SKILL.md`、`mcporter`、memory_type 枚举值。vendor `GRADER_SYSTEM_PROMPT`（内部子代理）不动。

### 并发实施分组（无文件冲突）

- **Group A（核心删除 + catalog 扩充）**：kernel/config/*、harness_prompt_overrides.py、persona.py、subagent_prompts.py、team/search/fast prompt.py、sop/prompt_section.py、deferred_manager.py
- **Group B（系统段翻译，不同文件）**：skill/loader.py、skill_harness.py、skill_marketplace_tool.py、goal.py、env_var_prompt.py、sandbox_mcp_prompt.py、fast/search/team nodes.py

## 验证策略

1. **lint/typecheck**：ruff + mypy 全绿
2. **单元测试**：现有 harness/agent/config 测试更新后全绿
3. **不变量守护**：vendor SHA-256 快照、schema 相等性、middleware 顺序断言保留
4. **残留扫描**：grep `legacy|compact_en|HarnessMode|AGENT_HARNESS_MODE|select_harness_text|catalog_for_mode|get_active_harness_mode|normalize_harness_mode` 在 src/tests/frontend 中无残留（纯历史注释/不相关领域除外）
