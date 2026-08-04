# Research: search_agent / fast_agent 的 system prompt 构建与 harness 模式依赖

- **Query**: 摸清 search_agent、fast_agent 的 system prompt 构建，供"删除 legacy/compact_en、只保留 compact_zh"任务使用
- **Scope**: internal（src + deepagents 0.6.7 vendor 包 + tests + frontend 引用）
- **Date**: 2026-08-04
- **关键结论**：search/fast 的提示词常量在 **import 时** 由 `select_harness_text` 一次性解析；两者创建的 inner graph 每次都经过 deepagents `HarnessProfile`（persona.py 注册），因此 **search/fast 都走 HarnessLocalizationMiddleware**（并非仅主 agent）。删除 legacy/compact_en 后需固化的引用面见各节。

---

## ① search_agent prompt 分析表

文件：`src/agents/search_agent/prompt.py`（全文 67 行）

| 符号 | 行号 | 类型 | compact_zh 文本（逐字） | legacy / compact_en 摘要 |
|---|---|---|---|---|
| import | 10 | `from src.agents.core.harness_prompt_overrides import select_harness_text` | — | — |
| `_LEGACY_SANDBOX_SYSTEM_PROMPT` | 12-23 | 私有常量（仅作 legacy 分支入参） | — | 完整英文 Storage Architecture 表格（Sandbox Local / Remote Storage `/skills/`、URL upload 段） |
| `_LEGACY_SANDBOX_RUNTIME_SECTION` | 25-30 | 私有常量 | — | 英文 Sandbox Runtime 段，含 `{work_dir}` |
| `_LEGACY_DEFAULT_SYSTEM_PROMPT` | 32-38 | 私有常量 | — | 英文 File System 表格（`/workspace`、`/skills/`） |
| `SANDBOX_SYSTEM_PROMPT` | 41-47 | 模块级 `select_harness_text(...)` → **import 时解析** | `## 存储\nshell 仅操作当前沙箱 `work_dir`；`/skills/` 是远端虚拟存储，只能用文件工具。技能代码须先以 `transfer_file`/`transfer_path` 传入再执行。URL 用 `upload_url_to_sandbox(url, absolute_file_path)` 下载。` | compact_en：英文精简 3 句（同语义） |
| `SANDBOX_RUNTIME_SECTION` | 49-56 | 模块级 `select_harness_text(...)` | `## 沙箱运行时\n当前 sandbox work_dir：`{work_dir}`\nshell 文件和上传必须使用此前缀；除非用户要求，不把内部路径写入持久文档。` | compact_en：英文精简版 |
| `DEFAULT_SYSTEM_PROMPT` | 59-65 | 模块级 `select_harness_text(...)` | `## 文件\n`/workspace` 存持久文件；`/skills/` 是可编辑的数据库虚拟存储，绝非 shell 路径。用文件工具访问；技能代码须先传入真实工作区再执行。` | compact_en：英文精简版 |
| `DEFERRED_TOOL_GUIDE` | 67 | `= ""` | 空字符串 | **死代码**：全仓库无任何 import 方（grep 仅命中定义行） |

**模块级捕获**：本文件**无** `_HARNESS_MODE` 模块级捕获；直接调用 `select_harness_text`（内部读 `get_active_harness_mode()` → `settings.AGENT_HARNESS_MODE`）。

`select_harness_text` 定义：`src/agents/core/harness_prompt_overrides.py:27-29` — `legacy if mode=="legacy" else compact_en if mode=="compact_en" else compact_zh`。

### search 相关 select_harness_text 调用点全集

| 调用点 | 位置 | 时机 |
|---|---|---|
| `SANDBOX_SYSTEM_PROMPT` / `SANDBOX_RUNTIME_SECTION` / `DEFAULT_SYSTEM_PROMPT` | `search_agent/prompt.py:41,49,59` | 模块 import 时 |
| `TEAM_ROUTER_SYSTEM_PROMPT`、`SANDBOX_SYSTEM_PROMPT`、`SANDBOX_RUNTIME_SECTION` | `team_agent/prompt.py:41,81,86` | 模块 import 时（team 模式复用同模式） |
| `FILE_WORKSPACE_GUIDE` 等 4 个 guide + `SUBAGENT_TASK_GUIDE` + `DEFAULT/DETAILED_SUBAGENT_PROMPT` | `core/subagent_prompts.py:171,176,181,186,275,394,399` | 模块 import 时 |
| `build_role_subagent_section` 内 5 处 | `core/subagent_prompts.py:422,437,442,450,458` | **函数调用时**（每构建一次 role section 解析一次，仍读 import 时同一 mode） |

---

## ② search_agent nodes 注入时机

文件：`src/agents/search_agent/nodes.py`（510 行）

- **agent 创建入口**：`create_deep_agent`（deepagents 导入 line 10；调用 line 318-325）。外层 graph 每轮 `agent_node` 执行都会 **重新创建 inner graph**（无缓存）。
- **system prompt 来源**：`_create_backend_and_prompt`（line 433-510）返回：
  - 非沙箱 → `DEFAULT_SYSTEM_PROMPT`（line 457）
  - 沙箱 → `SANDBOX_SYSTEM_PROMPT`（line 498）
  - 传入 `create_deep_agent(system_prompt=system_prompt, ...)`（line 318）
- **`SANDBOX_RUNTIME_SECTION`**：`.format(work_dir=...)` 后追加进 `subagent_prompt_sections`（line 217）与 `_prompt_sections`（line 273），由 `SectionPromptMiddleware`（line 229 / 281）在 **每次模型请求** 注入。
- **`build_persona_skill_harness_section`**：import 来源 `src/infra/persona_preset/skill_harness.py:27`（nodes.py:60-65 import）；调用点 line 187-191（仅 `sandbox_backend` 非 None 时）；注入位置 line 220（subagent）与 line 275（main）→ SectionPromptMiddleware 每请求注入。
- **harness 模式相关分支**：nodes.py **没有**显式 harness 分支。但 `from src.agents.core.persona import build_persona_prompt_sections`（line 24）会触发 persona.py 的模块级 `register_harness_profile`（persona.py:107-127），进而 deepagents 在 `create_deep_agent` 时对 search 的 main + general-purpose 子代理应用 HarnessProfile（vendor graph.py:538, 674, 755）。

**注入时机总结（search）**：
1. **import 时**：`select_harness_text` 解析 3 个 prompt 常量；persona.py 注册 HarnessProfile（捕获 `_HARNESS_MODE`）。
2. **每轮（graph 编译时）**：`agent_node` 重新 `create_deep_agent` → deepagents 组装 `USER`(SANDBOX/DEFAULT_SYSTEM_PROMPT) + `CUSTOM`(behavior_guide) + `SUFFIX`，应用 `tool_description_overrides`（vendor graph.py:557）与 `materialize_extra_middleware()`（= `build_harness_extra_middleware` → ShortTodoListMiddleware + HarnessLocalizationMiddleware，vendor graph.py:755）。
3. **每次模型请求**：HarnessLocalizationMiddleware `wrap_model_call` → `_override`（harness_prompt_overrides.py:300-318）本地化 vendor system 段与工具 schema；SectionPromptMiddleware 注入 persona/skills/memory/运行时段落。

---

## ③ fast_agent prompt 分析表

文件：`src/agents/fast_agent/prompt.py`（全文 33 行）

| 符号 | 行号 | 类型 | compact_zh 文本（逐字） | legacy / compact_en 摘要 |
|---|---|---|---|---|
| import | 8 | `select_harness_text` | — | — |
| `_LEGACY_FAST_SYSTEM_PROMPT` | 10-19 | 私有常量 | — | 英文 File System 表格 + 跨会话记忆说明 |
| `FAST_SYSTEM_PROMPT` | 21-32 | 模块级 `select_harness_text(...)` → **import 时解析** | `## 文件\n`/workspace`：持久文件；`/skills/`：可编辑技能定义。\n\n记忆：`memory_retain` 仅存长期用户事实、偏好、约束和反馈，不存寒暄、问题、代码或临时状态。`<memory_index>` 仅作线索，依赖细节前用 `memory_recall`；删除用 `memory_delete`。` | compact_en：英文精简版（同语义） |
| `DEFERRED_TOOL_GUIDE` | 33 | `= ""` | 空字符串 | **死代码**：无 import 方 |

无 `_HARNESS_MODE` 模块级捕获（同 search）。

---

## ④ fast_agent 创建入口

- **agent 创建入口**：`fast_agent/nodes.py:8` 导入 `create_deep_agent`，调用点 **line 249-258**（`system_prompt=FAST_SYSTEM_PROMPT`，line 132/251）。外层 graph `fast_agent_node` 每轮重新创建 inner graph。
- **注册**：`fast_agent/graph.py:37` `@register_agent("fast")`；`src/agents/__init__.py:30-32` `discover_agents()` 统一导入 fast/search/team。
- **是否走 HarnessProfile / HarnessLocalizationMiddleware**：**走**。`fast_agent/nodes.py:24` import `build_persona_prompt_sections` → 触发 persona.py 模块级 `register_harness_profile`（provider key: anthropic/openai/google_genai，persona.py:124-126）。deepagents `create_deep_agent` 对每个 model 解析 provider profile（vendor graph.py:538 `_harness_profile_for_model`），并把 `materialize_extra_middleware()`（= `build_harness_extra_middleware(_HARNESS_MODE)`）追加到 main stack（vendor graph.py:755）与 general-purpose 子代理 stack（vendor graph.py:674）。
- 因此 fast 的工具 schema **不是**英文 vendor 默认：`tool_description_overrides`（vendor graph.py:557）在编译时改写，HarnessLocalizationMiddleware 在每请求 `localize_tool_for_model`（harness_prompt_overrides.py:229-244）用 `_ZH_TOOLS`/`_ZH_FIELDS` 覆盖 description 与 schema 字段。删除 legacy/compact_en 后此处无本地化缺口。
- **fast 独有**：无 `SANDBOX_RUNTIME_SECTION` 注入、无 `build_persona_skill_harness_section`（fast nodes.py 无 import）；`_prompt_sections` 仅 MAIN_AGENT_PROMPT_SECTIONS + persona + skills + memory + goal（nodes.py:215-227）。

---

## ⑤ skill_harness 分析

文件：`src/infra/persona_preset/skill_harness.py`（54 行）

- `build_persona_skill_harness_section(raw_hints, tools) -> str`（line 27-53）：**不引用 harness 模式**，无 `select_harness_text`/`_HARNESS_MODE`。仅当 tools 含 `install_skill` 且 raw_hints 为有效 `PersonaSkillHint` 列表时，输出固定英文 `## Persona skill capabilities` 段落。
- 注入范围：**仅 search_agent**（nodes.py:187-191，沙箱模式）与 **team_agent**（nodes.py:414/528 区域追加，见 grep）。fast_agent **不注入**（fast nodes.py 无 import）。
- 删除 legacy/compact_en **不影响**此文件；但注意其输出为英文固定文本（非 harness 本地化范围），不属于本任务改动面。

---

## ⑥ 潜在 bug 清单

每条：证据（文件:行号）+ 触发条件 + 后果。

### B1. 两层本地化的"同源但互补"关系 — 删除后仍协调
- 证据：层1 = `select_harness_text` import 时选 zh 文本（search_agent/prompt.py:41-65、fast_agent/prompt.py:21-32、subagent_prompts.py:171-186 等）；层2 = `HarnessLocalizationMiddleware._override`（harness_prompt_overrides.py:300-318）每次请求执行 `_localize_system`（line 269-287），仅对 `_replacements()`（line 249-264）列出的 4 个 vendor 哨兵串做 `str.replace`：`FILESYSTEM_SYSTEM_PROMPT`、`EXECUTION_SYSTEM_PROMPT`、`TASK_SYSTEM_PROMPT`（deepagents.middleware 常量）与 `VENDOR_AVAILABLE_AGENTS_HEADING = "Available subagent types:"`（line 26）。
- 触发条件：无（正常路径）。
- 后果：两层文本不相交（zh 应用文本不含 vendor 英文哨兵串），互补不重叠。删除 legacy/compact_en 后两层仍协调 —— **无双重本地化冲突**。注意 `replace` 是全文替换，若未来 zh 文本中混入 vendor 哨兵串会误替换，当前不存在。

### B2. vendor 哨兵串版本漂移 → 英文段静默泄漏（删除后仍存在）
- 证据：`_replacements` 硬编码精确匹配 vendor 常量（harness_prompt_overrides.py:249-264）；`pyproject.toml:21` 依赖 `deepagents>=0.5.3`（当前安装 0.6.7）。
- 触发条件：deepagents 升级后 `FILESYSTEM_SYSTEM_PROMPT`/`EXECUTION_SYSTEM_PROMPT`/`TASK_SYSTEM_PROMPT`/agents 标题串任一变动。
- 后果：`replace` 不命中 → 英文 vendor 段静默进入 zh prompt，破坏"逐字不变"验收。**建议锁定 deepagents 版本**并加快照测试。

### B3. `catalog_for_mode("legacy")` 直接 raise ValueError — 当前全调用点已加护栏，删除后无路径
- 证据：`catalog_for_mode` line 130-131 `raise ValueError("legacy uses native vendor middleware")`；调用点护栏：`build_short_todo_middleware` line 199（legacy → `[]`）、`build_harness_extra_middleware` line 323（legacy → `()`）、persona.py line 103/115（`if _HARNESS_MODE != "legacy"` 守卫）、模块级 line 330-338（legacy 走 `{}`/`""` 分支）、`get_memory_guide` line 219-223。
- 触发条件：当前 search/fast 无任何路径向 `catalog_for_mode` 传 legacy（全部 import 时由 `get_active_harness_mode()` 统一解析，且被 `!= "legacy"` 守卫）。compact_en 不 raise（line 131 `zh = mode == "compact_zh"` 三元）。
- 后果：无。删除 legacy 后 `catalog_for_mode` 应整体删除或硬编码 zh，护栏随之消失。

### B4. 模块级 `_HARNESS_MODE` 捕获 vs. 运行时改设置的不一致（删除后消除）
- 证据：`_HARNESS_MODE = get_active_harness_mode()` 模块级捕获：persona.py:32、subagent_prompts.py:18、team_agent/prompt.py:7、deferred_manager.py:20；`AGENT_HARNESS_MODE` 在 `RESTART_REQUIRED_SETTINGS`（constants.py:30，注释 "Harness profiles register at import time"）。
- 触发条件：运行时修改 `AGENT_HARNESS_MODE`（env/DB 热更新）不重启。
- 后果：import 时文本（层1）与 per-request middleware（层2，persona.py:118 闭包捕获同一 `_HARNESS_MODE`）始终保持一致，但均与新设置不同步 —— 表现"改了不生效"，属预期（restart-required）。删除模式机制后该问题整体消除。

### B5. 死导出污染（删除建议的目标）
- 证据：`TOOL_DESCRIPTION_OVERRIDES`、`SHORT_WRITE_TODOS_TOOL`、`SHORT_WRITE_TODOS_SYSTEM`、`SHORT_TASK_TOOL`、`SHORT_READ_FILE`、`SHORT_EXECUTE`、`build_todo_middleware`（harness_prompt_overrides.py:330-344）以及两个 `DEFERRED_TOOL_GUIDE = ""`（search_agent/prompt.py:67、fast_agent/prompt.py:33）在 src/tests 中 **零引用**（grep 仅命中定义处）。
- 触发条件：无。
- 后果：无运行时影响；删除时可直接移除。

### B6. 测试矩阵依赖三模式（删除后测试必须重构）
- 证据：`tests/agents/core/test_harness_prompt_overrides.py`：line 47-52（normalize/catalog 循环 compact_en+compact_zh）、line 66-69（options == ["legacy","compact_en","compact_zh"]）、line 255（`build_harness_extra_middleware("legacy") == ()`）、line 300-316（profile 断言 `materialize_extra_middleware()` 长度 2）、line 319-349（三模式子进程矩阵，断言 `"Be concise"` in compact_en、`"Core Behavior"` in legacy、`len(compact_zh) < len(legacy)`）、line 353-365（deferred_manager `_HARNESS_MODE` 子进程）；`tests/agents/test_sop_prompt_section.py:23-39`（`build_sop_guidance_section("compact_zh")` 与 monkeypatch `get_active_harness_mode -> "legacy"`）。
- 触发条件：删除 legacy/compact_en/HarnessMode 后运行。
- 后果：测试报错/断言失败，需按 PRD 更新为无模式形态；其中"逐字一致"可由 line 319-349 子进程脚本改造为"仅 compact_zh 单模式快照对比"。

### B7. search/fast 的 tool schema 已本地化（非英文 vendor 默认）
- 证据：persona.py:116-118 `tool_description_overrides` + `extra_middleware` 注册；vendor graph.py:557（`_apply_tool_description_overrides`）、755（`materialize_extra_middleware`）；`localize_tool_for_model` harness_prompt_overrides.py:229-244（`_ZH_TOOLS`/`_ZH_FIELDS`）。
- 触发条件：无。
- 后果：无缺口 —— 删除 legacy/compact_en 后 search/fast 的 schema/description 继续走 zh catalog，无需补本地化。

### B8. `localize_tool_for_model` 对 `task` 工具的保留逻辑
- 证据：harness_prompt_overrides.py:236-239 —— `tool.name == "task"` 时 **不**替换 description（因 SubAgentMiddleware 已渲染 `{available_agents}`）；`tool_descriptions["task"]` 模板含 `{available_agents}` 占位符（line 88/99）。
- 触发条件：渲染后的 task description 被误替换成含占位符模板。
- 后果：当前被正确规避（有测试 line 110-130 固定此契约）。删除 compact_en 时勿动此逻辑，`_ZH_TOOLS["task"]` 模板与 `{available_agents}` 占位符必须原样保留。

### B9. team_agent 复用 search 的 SANDBOX_* 常量
- 证据：team_agent/nodes.py:32-37 import `SANDBOX_RUNTIME_SECTION as SEARCH_...`、`SANDBOX_SYSTEM_PROMPT as SEARCH_...`；line 84-89 `build_no_team_fallback_system_prompt`、line 261/307/414/528 使用；team_agent/prompt.py:81-88 另有自己的 SANDBOX_SYSTEM_PROMPT/SANDBOX_RUNTIME_SECTION。
- 触发条件：删除 search_agent/prompt.py 中的 select_harness_text 分支时若只改 search 不改 team 复用点。
- 后果：行为分裂。删除时 team 复用点自动获得硬编码 zh（常量改名后需同步 alias），且 team_agent/prompt.py:100-107,128 的 `_HARNESS_MODE == "compact_zh"` 三元标签（`成员`/`能力`/`指令`/`## 团队指令`）需一并固化。

### B10. 前端设置面残留
- 证据：`src/kernel/config/definitions.py:208-215`（SELECT 设置 options=["legacy","compact_en","compact_zh"]）；`src/kernel/config/base.py:30-39,92,466-469,526-527`（HarnessMode/validate/normalize）；`frontend/src/i18n/locales/zh.json:2169` 与 `en.json:2169`（AGENT_HARNESS_MODE 文案）、`zh.json:2615`/`en.json:2615`（"harness" 子分类名）、`frontend/src/components/panels/SettingsPanel.tsx:180`（subcategory harness）。
- 触发条件：按 PRD 删除设置项后。
- 后果：settings UI 出现"harness 子分类无设置项"残留（PRD 开放问题已记录）；需决定子分类去留。

---

## ⑦ 删除建议（范围清单）

按 `src/` + `tests/` + `frontend/` + `docs` 分面：

1. **search_agent/prompt.py**：删除 `_LEGACY_*` 3 个常量与 `select_harness_text` 包裹，3 个常量的 compact_zh 文本直接赋值（`SANDBOX_SYSTEM_PROMPT`/`SANDBOX_RUNTIME_SECTION`/`DEFAULT_SYSTEM_PROMPT`）；删除死常量 `DEFERRED_TOOL_GUIDE`。机器契约串（`transfer_file`、`upload_url_to_sandbox(url, absolute_file_path)`、`{work_dir}`）必须逐字保留。
2. **fast_agent/prompt.py**：同法固化 `FAST_SYSTEM_PROMPT`（compact_zh 文本；`memory_retain`/`memory_recall`/`memory_delete`/`<memory_index>` 契约串保留）；删除 `DEFERRED_TOOL_GUIDE`。
3. **harness_prompt_overrides.py**：删除 `select_harness_text`、`catalog_for_mode` 的 mode 参数与 EN/legacy 分支、`COMPACT_EN_BEHAVIOR_GUIDE`、`_EN_TOOLS`/`_EN_FIELDS`、`HarnessMode` 依赖；`build_short_todo_middleware`/`build_harness_extra_middleware` 去掉 mode 参数，直接走 zh catalog；删除 B5 死导出（line 330-344）。`HarnessLocalizationMiddleware`、`localize_tool_for_model`、`_replacements`、`build_short_todo_middleware` 保留（无模式形态）。
4. **persona.py**：`_HARNESS_MODE` 相关分支全删；`DEFAULT_ROLE` 固化中文（line 47-49）；`_BEHAVIOR_GUIDE` 直接 = `COMPACT_ZH_BEHAVIOR_GUIDE`；HarnessProfile 注册移除 legacy 分支（保留 `extra_middleware`/`excluded_middleware`/`tool_description_overrides` 的 zh 路径）；删 `_legacy_behavior_guide`。
5. **subagent_prompts.py**：4 个 guide + SUBAGENT_TASK_GUIDE + 2 个 SUBAGENT_PROMPT + role section 全部固化 zh；`get_memory_guide` 去掉 legacy 分支；删 `_LEGACY_*`、`_COMPACT_EN_*`、`_EN_*` 常量（注意 `_ZH_HANDOFF` 等保留，`Current task start time` 契约串不动）。
6. **team_agent/prompt.py + sop/prompt_section.py**：固化为 zh；`build_sop_guidance_section` 去掉 mode 参数（PRD 已列）；`build_team_members_description` 的 `成员/能力/指令` 三元固化。
7. **deferred_manager.py**：`_HARNESS_MODE` 三分支（line 23-48 与 235-250）固化 zh。
8. **kernel/config**：`base.py:30-39,92,466-469,526-527` 删 `HarnessMode`/`normalize_harness_mode`/`get_active_harness_mode`/`AGENT_HARNESS_MODE`；`constants.py:30` 从重启必需列表移除；`definitions.py:208-215` 删设置项。
9. **tests**：按 B6 清单重构。
10. **frontend**：i18n 文案与 SettingsPanel harness 子分类处理（B10）。

**最高风险点**：B2（vendor 串版本漂移破坏"逐字一致"）与 B9（team 复用 search 常量不同步）—— 删除后建议以 `tests/agents/core/test_harness_prompt_overrides.py:319-349` 的子进程快照脚本为基础，做"删除前 compact_zh vs 删除后"的逐字对比验证。

---

## Caveats / Not Found

- deepagents 包源码为 vendor 0.6.7（`.venv/Lib/site-packages/deepagents`），其 profile/中间件行为引用以该版本为准；升级需重验 B2。
- `src/agents/core/base.py:83-88` 的 `register_agent` 装饰器内示例注释引用 `@register_agent("search")`，与实际注册一致，无其他未发现的 agent 入口。
- `harness_prompt_overrides.py` 顶部注释"Reversible, provider-neutral harness compression"与 PRD 删除方向相反，删除时属纯注释范围，可不动或更新。
- 未找到任何 `.env.example` 或 main.py/run.py 中对 AGENT_HARNESS_MODE 的引用（grep 无命中）。
