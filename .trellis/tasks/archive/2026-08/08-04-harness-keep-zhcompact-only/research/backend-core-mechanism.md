# Research: harness 模式机制后端核心与配置定义（删除 legacy / compact_en，只保留 compact_zh）

- **Query**: 穷尽 src/kernel/config 与 src/agents/core 的 harness 模式机制；列出全部消费方、注入时机、潜在 bug 与删除建议，确保 compact_zh 行为逐字不变
- **Scope**: internal（含 deepagents 0.6.7 外部库 API 探测）
- **Date**: 2026-08-04
- **Task**: `.trellis/tasks/08-04-harness-keep-zhcompact-only`（PRD 见 `.trellis/tasks/08-04-harness-keep-zhcompact-only/prd.md`）

---

## ① 配置类型与设置定义清册（src/kernel/config/）

| 符号 | 文件:行 | 定义 / 默认值 | 删除影响 |
|---|---|---|---|
| `HarnessMode` | `src/kernel/config/base.py:30` | `Literal["legacy", "compact_en", "compact_zh"]` | 删除后 type 注解消失；Pydantic 字段类型改为 `str` 或直接删除字段 |
| `VALID_HARNESS_MODES` | `base.py:31` | `frozenset({"legacy","compact_en","compact_zh"})` | 删除 |
| `normalize_harness_mode` | `base.py:34-39` | strip/lower 后校验，非法值 raise `ValueError("Invalid AGENT_HARNESS_MODE …")` | 删除；唯一生产者是 `get_active_harness_mode`、field_validator 与测试 |
| `Settings.AGENT_HARNESS_MODE` | `base.py:92` | `AGENT_HARNESS_MODE: HarnessMode = "compact_zh"`（env 可加载，Pydantic 校验） | 删除字段；不再有环境变量/`.env` 开关 |
| `_normalize_agent_harness_mode` validator | `base.py:466-469` | `field_validator("AGENT_HARNESS_MODE", mode="before")` → `normalize_harness_mode` | 与字段一起删除 |
| `get_active_harness_mode` | `base.py:526-527` | `return normalize_harness_mode(settings.AGENT_HARNESS_MODE)`；读取模块级单例 `settings`（`base.py:522`，`@lru_cache get_settings()` L515-519） | 删除；所有调用方改硬编码 zh。注意它读的是**调用时**的 singleton，见 §⑤ |
| `AGENT_HARNESS_MODE` 设置定义 | `src/kernel/config/definitions.py:208-216` | `SettingType.SELECT` / `SettingCategory.AGENT` / `subcategory="harness"` / `settingDesc.AGENT_HARNESS_MODE` / default `"compact_zh"` / `options=["legacy","compact_en","compact_zh"]` | 删除定义。**`subcategory="harness"` 下只有这一项**（全仓 grep `"harness"` 仅此一处），删除后该子分类为空 |
| `RESTART_REQUIRED_SETTINGS` 条目 | `src/kernel/config/constants.py:29-30` | `"AGENT_HARNESS_MODE"`（注释：`Harness profiles register at import time.`） | 删除条目；`RESTART_REQUIRED_SETTINGS` 被 `src/infra/settings/storage.py:91,143`（`requires_restart` 标志）与 `src/infra/settings/service.py:257` 消费，残留条目只是给一个不存在的 key 打重启标志 |
| `__init__.py` 导出 | `src/kernel/config/__init__.py:7,9,11,31,32,33` | 导出 `HarnessMode` / `get_active_harness_mode` / `normalize_harness_mode` | 删除 import 与 `__all__` 条目 |
| `src/kernel/config.py`（单文件兼容层） | `config.py:1-32` | **未** re-export harness 符号（只 re-export Settings/常量/service） | 无需改动 |
| `src/kernel/config/service.py` | 全文件 | 无 harness 特判；`AGENT_HARNESS_MODE` 不在 `llm_affected` / `memory_affected` / `_CHECKPOINT_AFFECTED_SETTINGS` / `_SANDBOX_AFFECTED_SETTINGS` 任何热更新集合中 → 修改它只能靠重启生效 | 无需改动 |

前端残留（供主智能体分派，非本文件重点）：`frontend/src/i18n/locales/zh.json:2169`、`en.json:2169` 的 `settingDesc.AGENT_HARNESS_MODE`；`zh.json:2615` / `en.json:2615` 的 `subcategories.harness`；`frontend/src/components/panels/SettingsPanel.tsx:180` 的 `harness: t("subcategories.harness")`。`frontend/src/__tests__` 无 harness 引用。

---

## ② src/agents/core/harness_prompt_overrides.py 机制详表（全文 344 行）

| 符号 | 行 | 行为 | 删除后改法 |
|---|---|---|---|
| `VENDOR_AVAILABLE_AGENTS_HEADING` | 24 | 常量 `"Available subagent types:"`，`_replacements` 用它替换 vendor 标题 | 删除（`catalog.available_agents_heading` 同步删除，zh 值为 `"可用代理类型："`） |
| `select_harness_text(*, legacy, compact_en, compact_zh)` | 27-29 | 每次调用 `get_active_harness_mode()`；非 legacy/compact_en 一律落到 compact_zh（`else` 兜底） | 删除整个函数；调用方（见 §④）直接内联对应 zh 字符串常量 |
| `COMPACT_EN_BEHAVIOR_GUIDE` | 32-40 | 英文行为指南 | 删除 |
| `COMPACT_ZH_BEHAVIOR_GUIDE` | 42-50 | 中文行为指南（`compact_zh` 当前实际生效值） | 保留并提升为 `BEHAVIOR_GUIDE`（或按 PRD 开放问题决定是否去 `_ZH` 后缀）；**文本逐字不变** |
| `HarnessCatalog` dataclass | 53-62 | frozen；9 字段：`behavior_guide` / `write_todos_system` / `memory_guide` / `tool_descriptions` / `schema_fields` / `filesystem_system` / `execute_system` / `task_system` / `available_agents_heading` | 可整体删除（硬编码 zh 后由各常量直接承载），或保留为单一 zh catalog；二选一，需主智能体决策 |
| `_EN_WRITE_TODOS_TOOL` | 66-69 | 英文 write_todos 描述 | 删除 |
| `_ZH_WRITE_TODOS_TOOL` | 70-72 | 中文 write_todos 描述（含"恰好一项 in_progress / 禁止并行调用"） | 保留为硬编码常量 |
| `_EN_TOOLS`（10 工具描述 dict） | 75-86 | 英文工具描述（task 含 `{available_agents}` 模板） | 删除 |
| `_ZH_TOOLS` | 88-99 | 中文工具描述 | 保留为硬编码常量 |
| `_EN_FIELDS` | 101-110 | 英文 schema 字段描述 | 删除 |
| `_ZH_FIELDS` | 112-123 | 中文 schema 字段描述 | 保留为硬编码常量 |
| `catalog_for_mode(mode)` | 128-175 | **`if mode == "legacy": raise ValueError("legacy uses native vendor middleware")`**（129-130）；zh = `mode == "compact_zh"`，否则取 en 分支 | 删除函数；所有消费方（persona、subagent_prompts、tests）改为直接引用 zh catalog/常量 |
| `_ShortTodoListMiddleware` / `_short_todo_class()` | 180-195 | 惰性导入 `langchain.agents.middleware.TodoListMiddleware`，子类化（精确类型排除安全的 compact 版本），全局缓存 | 保留机制，去掉 mode 分支 |
| `build_short_todo_middleware(mode=None)` | 197-206 | `selected = mode or get_active_harness_mode()`；`selected=="legacy"` 或 `_short_todo_class() is None` → 返回 `[]`；否则 `[cls(system_prompt=catalog.write_todos_system, tool_description=catalog.tool_descriptions["write_todos"])]` | 去掉 mode 参数，恒构造 zh 版本 |
| `_compact_schema(node, descriptions)` | 208-225 | 递归剥 `description`/`title`，再为 `properties` 中已知字段注入 zh 描述 | 保留（与语言无关的机制） |
| `localize_tool_for_model(tool, catalog)` | 227-246 | 仅对 `BaseTool` 且 `name in catalog.schema_fields` 的已知工具 `deepcopy(args_schema)` + `model_copy(update={args_schema, description})`；`task` 工具 description 永不覆盖（保留 SubAgentMiddleware 渲染后的 `{available_agents}` 展开结果）；**`model_copy` 保留 `extras`（cache_control）**；未知工具原样返回 | 保留；catalog 参数恒传 zh |
| `_replacements(catalog)` | 248-265 | 惰性导入 deepagents 的 `FILESYSTEM_SYSTEM_PROMPT`/`EXECUTION_SYSTEM_PROMPT`/`TASK_SYSTEM_PROMPT`，加上 `VENDOR_AVAILABLE_AGENTS_HEADING`，返回 4 组替换对 | 保留机制，常量恒为 zh 值 |
| `_localize_system(message, catalog)` | 267-287 | 对 system message 的 str/list 文本块做 str.replace（不动非文本块）；无变化时返回原对象 | 保留 |
| `HarnessLocalizationMiddleware` | 290-319 | `AgentMiddleware` 子类；`_override`（296-305）本地化 system + 工具（仅当有变化才 `request.override`，zip strict）；`wrap_model_call`/`awrap_model_call`（308-319）包 handler | 保留；构造参数恒传 zh catalog |
| `build_harness_extra_middleware(mode=None)` | 322-328 | `selected = mode or get_active_harness_mode()`；**legacy → 返回空 `()`**；否则 `(*build_short_todo_middleware(selected), HarnessLocalizationMiddleware(catalog))` | 去掉 mode 参数；恒返回 `(*build_short_todo_middleware(), HarnessLocalizationMiddleware(zh_catalog))`（顺序不可变，见 §⑥-B5） |
| 模块级 `_mode = get_active_harness_mode()` | 330 | **import 时解析**（早于 `initialize_settings` 覆写，见 §⑤） | 删除 |
| `TOOL_DESCRIPTION_OVERRIDES` | 332/337 | legacy → `{}`；否则 `_catalog.tool_descriptions` | **全仓 grep 无任何消费方（含 tests）——死代码**，可删 |
| `SHORT_WRITE_TODOS_TOOL` | 333/338 | legacy → `""` | **死代码**，可删 |
| `SHORT_WRITE_TODOS_SYSTEM` | 334/339 | legacy → `""` | **死代码**，可删 |
| `SHORT_TASK_TOOL` / `SHORT_READ_FILE` / `SHORT_EXECUTE` | 341-343 | `TOOL_DESCRIPTION_OVERRIDES.get(...)` | **死代码**（无消费方），可删 |
| `build_todo_middleware` alias | 344 | `= build_short_todo_middleware` | **死代码**（无消费方），可删 |

> **死代码确认**：对 `TOOL_DESCRIPTION_OVERRIDES|SHORT_TASK_TOOL|SHORT_READ_FILE|SHORT_EXECUTE|SHORT_WRITE_TODOS|build_todo_middleware|HarnessCatalog` 的**全仓** grep（含 tests/、frontend/）仅命中 `harness_prompt_overrides.py` 自身与 `tests/agents/core/test_harness_prompt_overrides.py`（后者只引用 `HarnessCatalog` 类型，用于构造测试参数）。这些模块级常量是 07-20 重构遗留，删除零影响。

---

## ③ src/agents/core/persona.py HarnessProfile 注册详表

| 项 | 行 | 内容 |
|---|---|---|
| `_HARNESS_MODE` | 32 | `get_active_harness_mode()` **import 时捕获** |
| deepagents 探测 | 34-43 | `importlib.import_module("deepagents")`；`_HarnessProfile = getattr(deepagents,"HarnessProfile",None)`；`_register_harness_profile = getattr(deepagents,"register_harness_profile",None)`；ImportError → None（注册整体跳过） |
| `DEFAULT_ROLE` | 47-49 | `"你是具备工具和技能的智能助手。"` if `_HARNESS_MODE == "compact_zh"` else `"You are an intelligent assistant with tools and skills."` |
| `_legacy_behavior_guide()` | 88-98 | legacy 专用：从 `deepagents.graph.BASE_AGENT_PROMPT` 重建"压缩前"base prompt（保留 persona 权威） |
| `_BEHAVIOR_GUIDE` | 101-104 | legacy → `_legacy_behavior_guide()`；否则 `catalog_for_mode(_HARNESS_MODE).behavior_guide`（→ compact_zh 时即 `COMPACT_ZH_BEHAVIOR_GUIDE`） |
| 注册块（import 时执行） | 107-128 | `if _HarnessProfile is not None and _register_harness_profile is not None:`（110）`_profile_kwargs = {"base_system_prompt": _BEHAVIOR_GUIDE}`（114，**无条件**——legacy 也注册 base prompt）；`if _HARNESS_MODE != "legacy":`（115）加 `tool_description_overrides=_catalog.tool_descriptions`（117）+ `extra_middleware=lambda: build_harness_extra_middleware(_HARNESS_MODE)`（118-120，**闭包捕获 import 时模式**）；`if _HARNESS_MODE != "legacy" and _TodoListMiddleware is not None:`（121）`excluded_middleware=frozenset({TodoListMiddleware})`（122）；`_shared_profile = _HarnessProfile(**_profile_kwargs)`（124）；`for _provider_key in ("anthropic","openai","google_genai"): _register_harness_profile(_provider_key, _shared_profile)`（125-126）；`logger.info("[Harness] mode=%s", _HARNESS_MODE)`（128） |
| `split_persona_prompt` / `build_persona_prompt_sections` | 132-166 | 与模式无关；`build_persona_prompt_sections` 消费 `DEFAULT_ROLE`（159） |
| provider key 语义 | — | deepagents 0.6.7 `_harness_profile_for_model` 用 `model._get_ls_params()["ls_provider"]` 解析 provider（`anthropic`/`openai`/`google_genai`），与注册 key 匹配；`register_harness_profile` 是**叠加合并**（model 级 profile 字段覆盖 provider 级） |

**legacy 分支语义**：legacy 模式下 profile 仍注册，但只带 `base_system_prompt`（由 `_legacy_behavior_guide()` 从 vendor `BASE_AGENT_PROMPT` 派生），**无** `tool_description_overrides`、**无** `extra_middleware`（→ 不注入 `HarnessLocalizationMiddleware` 与 short todo）、**无** `excluded_middleware`（→ vendor 原生 `TodoListMiddleware` 保留）。这就是"legacy 用 vendor 原生 middleware"的确切含义。删除 legacy 后：`_legacy_behavior_guide`、legacy 分支、`_HARNESS_MODE` 全部删除，`_profile_kwargs` 恒为完整 zh 形态。

---

## ④ 消费方全清册

### 4.1 直接消费 harness 符号的 src 文件（7 个）

| 文件 | 消费符号 | 位置 | 删除影响 |
|---|---|---|---|
| `src/agents/core/persona.py` | `build_harness_extra_middleware`、`catalog_for_mode`、`get_active_harness_mode` | 19-21, 24, 32, 48, 101-104, 115-126, 128 | §③；注册块硬编码 zh |
| `src/agents/core/subagent_prompts.py` | `get_active_harness_mode`、`select_harness_text`、`catalog_for_mode`（函数内局部 import 221） | 12-18, 171-190, 275-279, 394-405, 422-466；`get_memory_guide` 216-224 | 各 `select_harness_text` 调用改 zh 常量；`_HARNESS_MODE` 判断改 zh 分支 |
| `src/agents/team_agent/prompt.py` | `get_active_harness_mode`、`select_harness_text`（从 harness_prompt_overrides 导入，5） | 5, 7, 41-59, 81-89, 100, 104, 107, 128 | 3 个 `select_harness_text` 改 zh 常量；`_HARNESS_MODE=="compact_zh"` 三元（member_label/能力/指令/团队指令标题）恒取 zh |
| `src/agents/team_agent/sop/prompt_section.py` | `HarnessMode`、`get_active_harness_mode`、`settings`（从 kernel.config 导入，10） | 12-50（三版本常量），53-64 `build_sop_guidance_section(mode=None)` | 删 `_LEGACY`/`_COMPACT_EN` 常量与 mode 参数，恒返回 `_COMPACT_ZH` 模板 |
| `src/agents/search_agent/prompt.py` | `select_harness_text`（10） | 41, 49, 59 | 3 处改 zh 常量（`_LEGACY_*` 删） |
| `src/agents/fast_agent/prompt.py` | `select_harness_text`（8） | 21-31 | 1 处改 zh 常量（`_LEGACY_FAST_SYSTEM_PROMPT` 删） |
| `src/infra/tool/deferred_manager.py` | `get_active_harness_mode`、`settings`（从 kernel.config 导入，14） | 20, 23-48, 235-257 | `_HARNESS_MODE` 三分支 → 恒取 compact_zh 文案；删 legacy/en 分支 |

### 4.2 间接消费（通过上述模块产出的常量，仅标注位置，深入分析由专门子智能体负责）

| 文件 | 位置 | 消费 |
|---|---|---|
| `src/agents/team_agent/nodes.py` | 24-29, 32-38（导入）；86-89 `build_no_team_fallback_system_prompt` 用 `SEARCH_SANDBOX_SYSTEM_PROMPT`/`FAST_SYSTEM_PROMPT`；205 `get_memory_guide()`；261 `FAST_SYSTEM_PROMPT`；307 `SEARCH_SANDBOX_SYSTEM_PROMPT`；470, 503 `SUBAGENT_PROMPT`；518 `MAIN_AGENT_PROMPT_SECTIONS`；536-538 `build_sop_guidance_section()`（**唯一生产调用点，无参→调用时读模式**） | 主代理/子代理 system prompt 组装 |
| `src/agents/search_agent/nodes.py` | 24-29, 33-37（导入）；164 `get_memory_guide()`；217 `SANDBOX_RUNTIME_SECTION.format`；250 `SUBAGENT_PROMPT`；266 `MAIN_AGENT_PROMPT_SECTIONS`；457 `DEFAULT_SYSTEM_PROMPT`；498 `SANDBOX_SYSTEM_PROMPT` | 同上 |
| `src/agents/fast_agent/nodes.py` | 23-28, 32（导入）；131 `get_memory_guide()`；134 `FAST_SYSTEM_PROMPT`；201 `SUBAGENT_PROMPT`；216 `MAIN_AGENT_PROMPT_SECTIONS` | 同上 |
| `src/infra/agent/middleware/tool_interception.py` | 36（导入 `DEFERRED_TOOL_SEARCH_GUIDE`）；618 `_system_message_contains_search_guide` 用它判重 | 延迟工具指引注入/去重 |

### 4.3 测试（4 个文件）

| 文件 | 依赖点 | 删除影响 |
|---|---|---|
| `tests/agents/core/test_harness_prompt_overrides.py` | 全文件；`normalize_harness_mode`(27,47-49)、`SETTING_DEFINITIONS["AGENT_HARNESS_MODE"]`(66-76)、`catalog_for_mode`(50,80,127,205,257,281)、`build_harness_extra_middleware("legacy")==()`(254)、`build_short_todo_middleware("compact_zh")`(91)、三进程启动测试(319-349)、deferred_manager 直导测试(352-364) | 重写为无模式形态；删除 legacy/en 参数化；保留 zh 断言 |
| `tests/agents/core/test_subagent_prompts.py` | 184-194 `_prompt_contract_for_mode` + 195 `@pytest.mark.parametrize("mode", ["legacy","compact_en","compact_zh"])` | 去参数化，只跑 zh 分支 |
| `tests/agents/test_sop_prompt_section.py` | 39 `monkeypatch.setattr(prompt_section, "get_active_harness_mode", lambda: "legacy")`；legacy 用例 12-22 | 删 legacy 用例与 monkeypatch；`build_sop_guidance_section()` 无参调用改 zh |
| `tests/kernel/config/test_sandbox_image_description_setting.py` | 30-39 `test_agent_harness_mode_moved_to_agent_category`（断言 AGENT_HARNESS_MODE 定义） | 删除该测试函数 |

### 4.4 前端（标注，供前端子智能体）

- `frontend/src/components/panels/SettingsPanel.tsx:180`（`SUBCATEGORY_LABELS.harness`）
- `frontend/src/i18n/locales/zh.json:2169`（`settingDesc.AGENT_HARNESS_MODE`）、`:2615`（`subcategories.harness`）；`en.json` 同两行

---

## ⑤ 注入时机分析（import-time vs call-time 对照）

**时间线**（同一进程）：

1. 进程启动 → 模块导入。`src.kernel/config/base.py:522` 创建单例 `settings = get_settings()`（`@lru_cache`，构造时读 env + `.env`）。
2. agent 侧 6 处模块级捕获**同时发生**（都早于 `initialize_settings`）：
   - `harness_prompt_overrides.py:330` `_mode`
   - `persona.py:32` `_HARNESS_MODE`
   - `subagent_prompts.py:18` `_HARNESS_MODE`
   - `team_agent/prompt.py:7` `_HARNESS_MODE`
   - `deferred_manager.py:20` `_HARNESS_MODE`
   - 以及所有模块级 `select_harness_text(...)`（team_agent/prompt.py:41,81,86；search_agent/prompt.py:41,49,59；fast_agent/prompt.py:21；subagent_prompts.py:171,176,181,186,275,394,399,422,437,442,450,458）——虽然 `select_harness_text` 是"调用时解析"，但生产调用点全部在模块 import 时执行，**等价于 import-time**。
3. `persona.py:118-120` 的 `extra_middleware` lambda **闭包捕获 import 时** `_HARNESS_MODE`。
4. 启动阶段 `initialize_settings()`（`src/kernel/config/service.py:256-304`）用 DB 值 `setattr(settings, "AGENT_HARNESS_MODE", db_value)` **原地改写同一 singleton**（DB > env > default）。
5. 请求运行时，**唯一的 call-time 重读**是 `team_agent/nodes.py:536-538` → `build_sop_guidance_section()`（`prompt_section.py:55` `get_active_harness_mode()`）→ 读到的是 **DB 值**。

**结论**：若 DB 中 `AGENT_HARNESS_MODE` 与 env/默认值不一致（如实验期在 DB 设过 `compact_en`/`legacy`），同一进程内**可能语言混搭**：模块级 prompt + deepagents profile 用 import 时模式（通常 zh），而 team SOP 引导段用 DB 值（en/legacy）。`get_active_harness_mode()` 每次重算不会使 import 时常量过期——因为那些常量根本不重算，两者各持己见。这是当前模式机制下真实存在的隐患；hardcode zh 后整类问题消失。

**热更新**：`refresh_settings(key)`（service.py:307-357）对 `AGENT_HARNESS_MODE` 无任何特判（不在 llm/memory/checkpoint/sandbox 集合内），且该 key 在 `RESTART_REQUIRED_SETTINGS` → 设计上只能重启生效。import 时捕获值不会"过期"于热更新，但会"偏离"于 DB 覆写（见上）。

---

## ⑥ 潜在 Bug 清单（每条含证据 + 触发条件 + 后果）

- **B1（混语言）**：import-time 捕获 vs call-time 重读不一致。
  证据：`persona.py:32` / `subagent_prompts.py:18` / `team_agent/prompt.py:7` / `deferred_manager.py:20`（import 时）vs `prompt_section.py:55`（调用时，经 `nodes.py:538`）。
  触发：DB 中 `AGENT_HARNESS_MODE` ≠ env/默认值（实验期改过设置）。后果：主代理其余 prompt 是 zh，而 SOP 引导段是 en 或 legacy 完整版；或反之。当前默认部署（DB 无该行或同为 compact_zh）不触发。hardcode 后消失。

- **B2（catalog_for_mode("legacy") raise）**：`harness_prompt_overrides.py:129-130` 对 legacy 直接 `raise ValueError`。
  证据：所有生产调用方都先挡 legacy——`persona.py:103`(`if _HARNESS_MODE=="legacy"`)、`persona.py:115,121`(`!= "legacy"`)、`subagent_prompts.py:219`(`== "legacy"`)、`build_short_todo_middleware:199`、`build_harness_extra_middleware:324`；测试从不对 legacy 调 `catalog_for_mode`。
  触发：无已知路径（`mode or get_active_harness_mode()` 的 falsy 回退也不会产生 "legacy" 之外的非法值，因为 validator 挡非法值）。后果：当前**不可触发**，但属潜伏陷阱——任何人新增未防护的 `catalog_for_mode(get_active_harness_mode())` 在 legacy 下会启动/首请求崩溃。删除 legacy 后陷阱移除。

- **B3（空 tuple 假设）**：legacy 下 `build_harness_extra_middleware` 返回 `()`。
  证据：`harness_prompt_overrides.py:324-325`；唯一消费者 `persona.py:118-120` 经 deepagents `materialize_extra_middleware()` → `list(_resolve_middleware_seq(()))` = `[]`，安全；测试 `test_harness_prompt_overrides.py:254` 显式断言 `== ()`；`test_shared_profile_resolves_for_supported_adapters:317` 断言 `len(...)==2` 只在非 legacy。触发：无。后果：无崩溃；但若未来有人解包 `mw[0], mw[1]` 则 legacy 下 IndexError。

- **B4（legacy 下 profile 仍注入 base_system_prompt）**：`persona.py:114` 无条件 `_profile_kwargs={"base_system_prompt": _BEHAVIOR_GUIDE}`，legacy 时取 `_legacy_behavior_guide()`（persona.py:88-98，从 deepagents `BASE_AGENT_PROMPT` 派生）。
  证据：见 §③。触发：mode=legacy。后果：legacy 并非 100% "vendor 原生"——base prompt 被替换为压缩前推导版；`catalog_for_mode` 注释 "legacy uses native vendor middleware"（129）是近似说法。删除 legacy 时须同时删 `_legacy_behavior_guide` 与 101-104 分支，避免残留。

- **B5（prompt-cache 断点/工具顺序）**：schema 本地化对模型可见工具做 `model_copy`，`extras`（`cache_control`）必须保留，工具顺序不可变。
  证据：`localize_tool_for_model`（227-246）`model_copy` 保留 extras；`HarnessLocalizationMiddleware._override`（296-305）仅在有变化时覆盖 tools；测试 `test_schema_localization_preserves_cache_extras_and_unknown_tools`（test_harness_prompt_overrides.py:190-206）钉死 extras 保留；`prompt_caching.py`（src/infra/agent/middleware/prompt_caching.py:30-59）在最内层重打 cache_control。07-20 result.md 亦声明 "Cache extras, tool order, middleware order, and stable/session/volatile prompt boundaries remain intact"。
  触发：hardcode 重构时若改动 `build_harness_extra_middleware` 的**返回顺序**（short-todo 必须在 HarnessLocalizationMiddleware 之前）、改动 zh 工具文本之外的 schema 结构、或新增/删除工具条目。后果：cache 断点偏移（命中率下降）或工具可见性意外变化。**行为等价验收应包含对 extras/顺序/断点的比对**。

- **B6（harness 子分类空壳）**：删除 `AGENT_HARNESS_MODE` 定义后 `subcategory="harness"` 无其他设置项。
  证据：definitions.py:208-216 是全仓唯一 `"harness"` subcategory（grep 确认）；`SettingsPanel.tsx:180` + i18n 仍有 `subcategories.harness`。
  触发：删除设置项后。后果：后端不再返回该分组（UI 组消失）；前端 label 成死代码。需按 PRD 决定删除前端残留。

- **B7（`select_harness_text` else 兜底掩盖非法值）**：`harness_prompt_overrides.py:29` 凡非 legacy/compact_en 一律返回 compact_zh。
  证据：validator（base.py:466-469）本应挡非法值，但 `select_harness_text` 走的是 singleton 现值，若未来有人绕过 validator 注入非法值会被静默当 zh。hardcode 后消失。

- **B8（`mode or ...` falsy 回退）**：`build_short_todo_middleware`（198）与 `build_harness_extra_middleware`（323）的 `mode or get_active_harness_mode()`，传 `""`/`0` 会静默回退。当前调用方无此用法；删参数后陷阱消失。

- **B9（测试/文档连带）**：见 §4.3 四个测试文件 + `.trellis/spec/backend/agent-harness.md`（PRD 验收要求同步更新）。`uv.lock` 固定 `deepagents==0.6.7`（.venv 实测），pyproject 声明 `>=0.5.3`；vendor system prompt SHA-256 快照测试（test_harness_prompt_overrides.py:135-157）依赖 deepagents 具体文案，重构时**不得改动 zh 目标文本**，且 vendor 快照断言保留。

- **B10（`initialize_settings` 前导入顺序假设）**：所有模块级捕获假设 agent 模块在 `initialize_settings` 之前 import（当前成立：main.py → api 路由 → AgentFactory → nodes → prompts 全在 lifespan 前）。若未来有人改为动态导入 agent 模块（运行时 `importlib`），捕获值将从 DB 值而非 env 值出发，与既有模块级常量分叉。hardcode 后无此问题。

---

## ⑦ 删除建议（每处怎么删/改 + hardcode zh 等价改法）

> 原则：**compact_zh 渲染出的 system prompt / 工具 schema 必须逐字不变**；工具名、schema 属性名、required/enum/default、`Current task start time` 哨兵行、`{available_agents}` 模板语义、middleware 顺序、tools 顺序全部冻结。

1. **`src/kernel/config/base.py`**：删 `HarnessMode`(30)、`VALID_HARNESS_MODES`(31)、`normalize_harness_mode`(34-39)、`AGENT_HARNESS_MODE` 字段(92)、validator(466-469)、`get_active_harness_mode`(526-527)。`__init__.py` 同步删 3 个导出。`constants.py` 删条目(29-30)。`definitions.py` 删定义(208-216)。

2. **`src/agents/core/harness_prompt_overrides.py`**（核心）：
   - `select_harness_text`(27-29) 删除；调用方内联 zh。
   - `catalog_for_mode`(128-175) 删除（或改为 `ZH_CATALOG = HarnessCatalog(...)` 单一实例）；`_EN_*`/`COMPACT_EN_*`/`VENDOR_AVAILABLE_AGENTS_HEADING` 删除；`_ZH_*`/`COMPACT_ZH_*` 保留为顶层常量（去 `_ZH` 后缀与否按 PRD 开放问题，**文本不变**）。
   - `build_short_todo_middleware()`(197-206) 去掉 mode 参数与 legacy 分支，恒返回 zh `[cls(...)]`。
   - `build_harness_extra_middleware()`(322-328) 去掉 mode 参数，恒返回 `(*build_short_todo_middleware(), HarnessLocalizationMiddleware(ZH_CATALOG))`——**顺序严格保持**。
   - 模块级 `_mode`(330) 与 `TOOL_DESCRIPTION_OVERRIDES`/`SHORT_WRITE_TODOS_TOOL`/`SHORT_WRITE_TODOS_SYSTEM`/`SHORT_TASK_TOOL`/`SHORT_READ_FILE`/`SHORT_EXECUTE`/`build_todo_middleware`(332-344) 全部删除（死代码，无消费方）。

3. **`src/agents/core/persona.py`**：删 `_HARNESS_MODE`(32) 及 `get_active_harness_mode` import(24)；`DEFAULT_ROLE`(47-49) 恒 zh；`_legacy_behavior_guide`(88-98) 删；`_BEHAVIOR_GUIDE`(101-104) 恒 `COMPACT_ZH_BEHAVIOR_GUIDE`；注册块(107-128) 恒为完整 kwargs（base_prompt + tool_description_overrides + extra_middleware + excluded_middleware），三个 provider key 与 import 时注册时机保留。

4. **`src/agents/team_agent/sop/prompt_section.py`**：删 `_LEGACY_*`/`_COMPACT_EN_*`(12-42)；`build_sop_guidance_section()`(53-64) 去 mode 参数与三分支，恒返回 `_COMPACT_ZH_*` 模板（`TEAM_SOP_MIN/MAX_STEPS` 格式化保留）。

5. **三个 agent prompt 模块**：
   - `team_agent/prompt.py`：3 个 `select_harness_text`(41-59,81-89) 内联 zh 文本（删 `_LEGACY_*`）；`_HARNESS_MODE`(7) 删除；`build_team_members_description`(100-107) 与 `build_team_router_system_prompt`(128) 的 zh 三元恒取 zh 分支。
   - `search_agent/prompt.py`：3 处(41,49,59) 内联 zh，删 `_LEGACY_*`。
   - `fast_agent/prompt.py`：1 处(21) 内联 zh，删 `_LEGACY_*`。

6. **`src/agents/core/subagent_prompts.py`**：`_HARNESS_MODE`(18) 删；4 个 guide 的 `select_harness_text`(171-190) 内联 zh（`_COMPACT_EN_*`/`_LEGACY_*` 删）；`get_memory_guide`(216-224) 删 legacy 分支恒返回 `ZH_CATALOG.memory_guide`；`SUBAGENT_TASK_GUIDE`(275-279)、`DEFAULT/DETAILED_SUBAGENT_PROMPT`(394-405)、`build_role_subagent_section` 各 heading(422-466) 内联 zh（`_EN_DEFAULT/_EN_DETAILED/_LEGACY_SUBAGENT_TASK_GUIDE` 删，`_ZH_HANDOFF` 保留）。

7. **`src/infra/tool/deferred_manager.py`**：`_HARNESS_MODE`(20) 删；`DEFERRED_TOOL_SEARCH_GUIDE`(23-48) 恒 zh 分支；hidden-count 文案(235-257) 恒 zh 分支；`get_active_harness_mode` import(14) 删。

8. **测试**：按 §4.3 重写（删模式枚举/参数化/legacy 用例/孤立进程 env 测试；保留 zh 断言、SHA-256 快照、middleware 顺序断言、extras 保留断言）。

9. **文档**：`.trellis/spec/backend/agent-harness.md` 更新为无模式契约（PRD 验收）；`07-20` result.md 的 rollback 矩阵（`AGENT_HARNESS_MODE=compact_en/legacy` 回退路径）失效，可加注说明。

10. **前端**（供前端子智能体）：删 `settingDesc.AGENT_HARNESS_MODE`（zh/en.json:2169）、`subcategories.harness`（zh/en.json:2615）、`SettingsPanel.tsx:180` label。

---

## 关键数据汇总

- **直接消费 harness 符号的 src 文件数**：7（persona / subagent_prompts / team_agent.prompt / team_agent.sop.prompt_section / search_agent.prompt / fast_agent.prompt / deferred_manager）
- **间接消费（nodes/tool_interception）**：4 个文件
- **受影响测试文件**：4
- **受影响前端位置**：3
- **harness_prompt_overrides.py 内死代码**：7 个模块级符号（`_mode`、`TOOL_DESCRIPTION_OVERRIDES`、`SHORT_WRITE_TODOS_TOOL`、`SHORT_WRITE_TODOS_SYSTEM`、`SHORT_TASK_TOOL`、`SHORT_READ_FILE`、`SHORT_EXECUTE`、`build_todo_middleware`）——无任何消费方，可安全删除

## 未覆盖 / 留给主智能体

- team_agent/search_agent/fast_agent nodes.py 内部深层 prompt 组装逻辑（专门子智能体负责，此处仅标注消费位置）
- deepagents 0.6.7 外部文档（仅本地源码探测：HarnessProfile 字段、register_harness_profile 叠加语义、provider 解析）——如需官方文档可查 https://docs.langchain.com/oss/python/deepagents
- 前端设置 UI 删除细节
