# Research: team_agent system prompt 构建与注入链路（供删除 legacy/compact_en、只保留 compact_zh 使用）

- **Query**: 彻底摸清 team_agent 的 system prompt 构建与注入链路；记录每处 legacy/compact_en 分支的删除影响与 compact_zh 逐字文本；定位 harness 模式混搭潜在 bug。
- **Scope**: internal（代码库只读调研）
- **Date**: 2026-08-04

## 结论速览

- team_agent 的 prompt 组装**全部发生在每次请求内**（`team_router_node` 内），但**模板常量在 import 时解析**（模块级 `select_harness_text` / `_HARNESS_MODE`）。两套时机并存是潜在混搭 bug 的根源。
- 默认模式 `compact_zh`（`.env:243`，`base.py:92`），当前实际运行行为 = 全中文。删除 legacy/compact_en、hardcode 中文 = 把"运行时选择"替换为"编译时字面量"，对默认运行无行为变化。
- **命名更正**：任务中提到的 `build_team_member_system_prompt` 在代码库中不存在。成员 prompt 的实际构建是 `nodes.py` 的 `build_role_subagent_section`（subagent_prompts.py）+ `SUBAGENT_PROMPT` 基底（subagent_prompts.py:410）+ `SectionPromptMiddleware` 注入。
- **重要发现**：`team_agent/prompt.py` 里的 `SANDBOX_SYSTEM_PROMPT` / `SANDBOX_RUNTIME_SECTION`（80-90 行）是**死代码**——`nodes.py` 实际导入的是 `search_agent/prompt.py` 的 `SEARCH_SANDBOX_*`（nodes.py:33-37）。若任务只改 team_agent/prompt.py 会漏掉真正生效的注入源。

---

## ① prompt.py 逐常量分析（src/agents/team_agent/prompt.py）

### 模块级捕获

| 位置 | 代码 | 时机 |
|---|---|---|
| prompt.py:5 | `from ...harness_prompt_overrides import get_active_harness_mode, select_harness_text` | import |
| prompt.py:7 | `_HARNESS_MODE = get_active_harness_mode()` | **import 时**（模块导入即固化） |

`_HARNESS_MODE` 只在 4 处条件分支使用：`prompt.py:100,104,107,128`。**没有**成员级/请求级重捕获。

### 常量三分支表

| 常量 | 行号 | legacy 文本（摘要） | compact_en（摘要） | compact_zh（摘要） | nodes.py 是否使用 |
|---|---|---|---|---|---|
| `TEAM_ROUTER_SYSTEM_PROMPT` | 41-59 | `_LEGACY_TEAM_ROUTER_SYSTEM_PROMPT`（9-39 行）："You are a team router agent..." 完整英文、`## Team Composition`、`## Routing Rules` 8 条、`## Output` | 44-50 行精简英文："You route work across a team: understand, split, assign via `task`, then verify and synthesize. ## Team ... Default role ... Route by role fit..." | 51-58 行中文（见下） | **是**（经 `build_team_router_system_prompt` → nodes.py:253-258 → create_deep_agent system_prompt） |
| `SANDBOX_SYSTEM_PROMPT` | 80-85 | `_LEGACY_SANDBOX_SYSTEM_PROMPT`（61-74 行）表格版 | 82-83 行："Shell uses sandbox `work_dir`; `/skills/` is remote virtual storage..." | 84 行中文 | **否（死代码）** — nodes.py 用 `SEARCH_SANDBOX_SYSTEM_PROMPT` |
| `SANDBOX_RUNTIME_SECTION` | 86-90 | `_LEGACY_SANDBOX_RUNTIME_SECTION`（76-79 行） | 88 行英文 | 89 行中文 | **否（死代码）** — nodes.py 用 `SEARCH_SANDBOX_RUNTIME_SECTION` |

### select_harness_text 调用点（team_agent 内）

- `prompt.py:41`（TEAM_ROUTER_SYSTEM_PROMPT）、`prompt.py:81`（SANDBOX_SYSTEM_PROMPT）、`prompt.py:86`（SANDBOX_RUNTIME_SECTION）。全部 import 时求值。
- `select_harness_text` 内部（harness_prompt_overrides.py:27-29）：`mode = get_active_harness_mode()` → **调用时**读取 `settings.AGENT_HARNESS_MODE`。对模块级常量而言"调用时"= import 时。

### 删除后保留的 compact_zh 文本（逐字，供 hardcode）

**TEAM_ROUTER_SYSTEM_PROMPT compact_zh（prompt.py:51-58，含 format 占位符）：**
```
你负责团队路由：理解请求、拆分任务、用 `task` 分派、核验并整合。

## 团队
{team_members_description}
{team_instructions_section}
默认角色：{default_role}。

按角色能力分派实际工作，不发送协调/提醒消息；独立任务并行，转交用户时间戳。收齐结果后以证据消解冲突，明确失败，最终只输出统一答案。
```
（占位符：`{team_members_description}` `{team_instructions_section}` `{default_role}`，由 `build_team_router_system_prompt` 的 `.format(...)` 填充，prompt.py:130-137。**占位符必须原样保留**，`{` 在 zh 文本中无其他出现，可安全 format。）

**SANDBOX_SYSTEM_PROMPT compact_zh（prompt.py:84）：**（死代码，见上；若保留而非删除）
```
shell 仅操作沙箱 `work_dir`；`/skills/` 是远端虚拟存储。技能代码先传入再执行；`upload_url_to_sandbox` 必须使用沙箱绝对路径。
```

**SANDBOX_RUNTIME_SECTION compact_zh（prompt.py:89）：**（死代码；含 `{work_dir}` 占位符）
```
当前 sandbox work_dir：`{work_dir}`。shell 文件/上传均使用此前缀；非用户要求不得持久化。
```

### 成员 prompt 构建函数（prompt.py）

| 函数 | 行号 | 签名 | harness 分支（_HARNESS_MODE，import 时固化） |
|---|---|---|---|
| `build_team_members_description(team, role_summaries=None)` | 92-109 | 逐成员生成 router 描述行 | 100: `member_label = "成员" if _HARNESS_MODE == "compact_zh" else "member_id"`；104: `label = "能力" if ... else "Capability summary"`；107: `label = "指令" if ... else "Instructions"` |
| `build_team_router_system_prompt(team, *, default_role, role_summaries=None)` | 120-137 | 组装 router 完整 prompt | 128: `heading = "## 团队指令" if _HARNESS_MODE == "compact_zh" else "## Team Instructions"`；130-137: `TEAM_ROUTER_SYSTEM_PROMPT.format(...)` |
| `build_team_subagent_display_names` | 139-146 | 内部类型→角色名映射 | 无 harness 分支 |
| `build_team_subagent_avatars` | 148-155 | 内部类型→头像映射 | 无 harness 分支 |
| `build_team_member_subagent_type` | 157-165 | slug 化 subagent 类型 | 无 harness 分支（非 ASCII 角色名回退 "role"，测试 test_team_router.py:143-151 断言） |
| `summarize_role_system_prompt` | 111-118 | 角色能力摘要（500 字截断） | 无 harness 分支 |

**关键点**：`build_team_members_description` / `build_team_router_system_prompt` 是**每请求调用**（nodes.py:253-258），但其中 harness 判断用的是 **import 时固化**的 `_HARNESS_MODE`。

---

## ② 成员 prompt 构建函数签名与调用点

实际链路由 3 个文件拼成（每次请求、每个 member 循环执行）：

1. **基底**：`SUBAGENT_PROMPT` = `DETAILED_SUBAGENT_PROMPT`（subagent_prompts.py:410, 399-408）——模块级 `select_harness_text`，**import 时固化**；zh 版含 `_ZH_HANDOFF`。注入点：nodes.py:472 `"system_prompt": SUBAGENT_PROMPT`。
2. **角色段**：`build_role_subagent_section(role_name, role_system_prompt, team_name=None, team_instructions=None, role_instructions=None, task_objective=None)`（subagent_prompts.py:415-467）——**调用时** `select_harness_text`（每次请求实时取模式）。调用点：nodes.py:429-438（成员循环内）。zh 分支逐字文本：
   - 422-426: `role_intro` = `f"你是 **{role_name}** 角色的子代理。"`
   - 437: 团队标题 heading = `"团队"`
   - 442: `"### 团队指令"`
   - 450: `"### 角色指令"`
   - 458: `"### 任务目标"`
3. **注入中间件**：`SectionPromptMiddleware(sections=role_prompt_sections)`（nodes.py:382-384，`_build_subagent_middleware` 内；prompt_sections 在 436-444 组装）。

role_prompt_sections 组装顺序（nodes.py:436-445）：role_section → role_skill_prompts / skills_prompt → memory_guide → sandbox_capability_section → subagent_runtime_section（`SEARCH_SANDBOX_RUNTIME_SECTION.format(work_dir=...)`，410-416）→ marketplace_skill_prompt。

`build_role_subagent_prompt`（subagent_prompts.py:477-492）是遗留完整拼接版，**team 路径未调用**（nodes.py 走 section 注入）。

---

## ③ nodes.py 注入时机（何时/何地进入模型请求）

`team_router_node`（nodes.py:127-672）**每次请求**完整重建内层 graph——prompt 全部在此函数内组装，无跨请求缓存（graph.py:84-88 外层仅 START→agent→END，无 checkpointer）。

| 注入物 | 组装位置 | 进入模型路径 | 时机 |
|---|---|---|---|
| Router 主 system_prompt = `build_team_router_system_prompt(...)` | nodes.py:253-258 | create_deep_agent `system_prompt=`（nodes.py:570） | 每请求；模板 import 时，成员描述每请求 |
| 无团队回退 = `FAST_SYSTEM_PROMPT` | nodes.py:258；`build_no_team_fallback_system_prompt` 84-89 | 同上 | FAST_SYSTEM_PROMPT import 时（fast_agent/prompt.py:21） |
| 沙箱 prepend = `SEARCH_SANDBOX_SYSTEM_PROMPT` | nodes.py:305-306 `f"{SEARCH_SANDBOX_SYSTEM_PROMPT}\n\n{system_prompt}"`；308 行回退同样用它 | 同上 | import 时（search_agent/prompt.py:41） |
| 主代理 section 栈：`*MAIN_AGENT_PROMPT_SECTIONS`（FILE_WORKSPACE/REVEAL/SAFETY/TOOL_DISCOVERY/SUBAGENT_TASK_GUIDE） | nodes.py:519 | `SectionPromptMiddleware`（nodes.py:540） | **import 时**（subagent_prompts.py:170-189, 275-279 模块级 select_harness_text） |
| `sandbox_capability_section` | nodes.py:525-526 | 同上 | 每请求（build_sandbox_capability_section） |
| `SEARCH_SANDBOX_RUNTIME_SECTION.format(work_dir=...)` | nodes.py:528 | 同上 | 每请求；模板 import 时 |
| goal section | nodes.py:533-534 | 同上 | 每请求 |
| **`build_sop_guidance_section()`**（TEAM_SOP_MODE 且团队模式） | nodes.py:536-538 | 同上 | **每请求 + 调用时取模式**（sop/prompt_section.py:55） |
| 成员子代理 system_prompt = `SUBAGENT_PROMPT` | nodes.py:472 | SubAgent 定义 | import 时 |
| 成员角色段 = `build_role_subagent_section` | nodes.py:429-438 | 子代理 `SectionPromptMiddleware`（382-384） | 每请求 + **调用时取模式** |
| write_todos 过滤 | nodes.py:401, 566（`TeamToolExclusionMiddleware`） | wrap_model_call | 每请求 |

结论：**所有 prompt 在每次请求时组装修订**；但模板内容要么 import 时固化（常量），要么调用时实时（build_role_subagent_section / build_sop_guidance_section）。

---

## ④ context.py / sop / tool_exclusion 分析

### src/agents/team_agent/context.py
- `TeamAgentContext(FastAgentContext)`（24-25 行），复用 fast_agent 的工具/技能加载。
- **不引用任何 prompt 常量、不引用 harness 模式**。仅做工具裁剪（`TEAM_ROUTER_EXCLUDED_TOOLS` frozenset，13-21 行：ask_human/find_skills/install_skill/persona 管理/团队管理工具）与沙箱工具追加（upload_url_to_sandbox，44-47 行）。
- 裁剪注释（8-10 行）说明"deepagents 子代理默认共享主代理工具，因此裁剪同时覆盖成员子代理"。
- 删除 harness 分支对 context.py **零影响**。

### sop/（新目录，git 未跟踪）
| 文件 | 内容 | harness 相关性 |
|---|---|---|
| `prompt_section.py` | `build_sop_guidance_section(mode: HarnessMode | None = None)`，54-63 行 | **相关**（见下） |
| `schemas.py` / `store.py` | SOPPlan 模型、校验、持久化 | 无（仅 `mode="json"` 字符串，非 harness） |
| `tool.py` | `create_update_sop_tool`，工具 description 全中文 | 无 harness 分支 |

`build_sop_guidance_section` 分析：
- 导入 `HarnessMode, get_active_harness_mode, settings`（prompt_section.py:10）。
- 三分支：`_LEGACY_SOP_GUIDANCE_SECTION`（13-32）、`_COMPACT_EN_SOP_GUIDANCE_SECTION`（35-42）、`_COMPACT_ZH_SOP_GUIDANCE_SECTION`（45-49）。
- 唯一调用点 nodes.py:536-538，**不传 mode** → `selected = mode or get_active_harness_mode()` 取当前 settings 值。
- 模板 `.format(max_steps=settings.TEAM_SOP_MAX_STEPS, min_steps=settings.TEAM_SOP_MIN_STEPS)`（60-62）。zh 模板含 `[{min_steps}, {max_steps}]` 占位符——**必须保留 format**。
- 删除后保留的 compact_zh 逐字文本（prompt_section.py:45-49）：

```
## SOP 规划（复杂团队任务）
复杂/多角色任务：先调用 `update_sop`（action=create）提交完整 SOP DAG，确认后再逐步执行；简单问题：`update_sop` 设 `direct_answer=true` 直接回答，不建 DAG、不弹确认。

步骤写法：每步单一职责，产出可验证交付物（expected_output）；依赖显式声明且只引用已存在 step_id；无依赖的独立步骤可并行；assignee 选最匹配角色；步数控制在 [{min_steps}, {max_steps}]；自查粒度，过细合并、过粗拆分。

执行：确认后逐步完成，每个 `task` 描述带上本步要求与前驱关键输出；每步完成后调 `update_sop` 更新步骤状态与输出；步骤失败则更新状态、说明原因，必要时调整后续步骤；全部完成总结交付，`update_sop` 置计划 status=completed。
```

### tool_exclusion.py
- 模块级 `_WRITE_TODOS_HEADINGS = {"`write_todos`", "write_todos"}`（29 行）同时覆盖 compact（反引号）与 legacy（无反引号）两种标题——**模式无关**，两者都剥。删除 legacy 后仅反引号变体会出现，但保留两个无害。
- 注释 3-8 行解释背景："`write_todos` 由 harness 的 `ShortTodoListMiddleware`（compact 模式）或 deepagents 默认栈注入（legacy）"。**无模式判断代码**，`_excluded_tool_names` 固定为 `{"write_todos"}`。
- 删除影响：**零代码改动**；仅注释可随 legacy 删除而简化。SOP 工具（update_sop）为其正式替代（tool.py 全中文，与模式无关）。

---

## ⑤ search / fast 与 team 的关系

**team_agent 不把 search_agent / fast_agent 作为子代理调用**（无 import search 节点/调用点）。关系仅为：

1. **prompt 常量复用**（nodes.py:32-37）：
   - `FAST_SYSTEM_PROMPT` → 无团队回退主 prompt（nodes.py:258, 308）。
   - `SEARCH_SANDBOX_SYSTEM_PROMPT` → 团队模式沙箱 prepend（nodes.py:305-306）+ 无团队沙箱回退（nodes.py:308）。
   - `SEARCH_SANDBOX_RUNTIME_SECTION` → 主代理与子代理的沙箱运行时段（nodes.py:528, 414-416）。
   - 这些常量均为 search/fast 模块级 `select_harness_text`（import 时）。
2. **context 复用**：`TeamAgentContext(FastAgentContext)`（context.py:3,24）。
3. **共享 section**：`MAIN_AGENT_PROMPT_SECTIONS`、`SUBAGENT_PROMPT`、`build_role_subagent_section`、`get_memory_guide` 来自 `src/agents/core/subagent_prompts.py`（fast/search/team 共用，subagent_prompts.py:1-8 注释说明）。**这些文件在 team_agent/ 之外**——若删除目标是全仓 legacy/compact_en，则 subagent_prompts.py / fast_agent/prompt.py / search_agent/prompt.py / harness_prompt_overrides.py 的 harness 分支也需处理（超出本任务 team_agent 范围，需主 agent 决策边界）。
4. team 自己的子代理 = 团队成员（custom_subagents，nodes.py:419-484）或单 `general-purpose` 回退子代理（nodes.py:487-508，description 内联英文，与 harness 无关）。

---

## ⑥ 潜在 bug 清单

| # | 证据（文件:行号） | 触发条件 | 后果 |
|---|---|---|---|
| B1 | import 时固化 vs 调用时实时混搭：prompt.py:7（`_HARNESS_MODE`）、prompt.py:41/81/86（模块级 select_harness_text）、subagent_prompts.py:410/201-209、fast_agent/prompt.py:21、search_agent/prompt.py:41/49/59、persona.py:32、deferred_manager.py:20（全部 import 时）**vs** subagent_prompts.py:422-460（`build_role_subagent_section` 调用时）、sop/prompt_section.py:55（调用时） | 进程运行中 `AGENT_HARNESS_MODE` 被修改。`SettingsService.set()` → `refresh_settings(key)` 会**直接 setattr**（infra/settings/service.py:128-134；kernel/config/service.py:340-341），无 restart-required 拦截（constants.py:30 只是 UI/文档契约，spec agent-harness.md:25-27 明确"requires restart"但代码层不强制） | **同一进程内语言混搭**：router 主 prompt / FAST / SEARCH / SUBAGENT_PROMPT 用旧模式文本，而成员角色段（build_role_subagent_section）与 SOP 段（build_sop_guidance_section）用新模式文本 → 中文 prompt 配英文成员段（或反之） |
| B2 | 各模块 import 时机不同：prompt.py:7 vs subagent_prompts.py:18 vs persona.py:32 vs deferred_manager.py:20 vs harness_prompt_overrides.py:330（`TOOL_DESCRIPTION_OVERRIDES` 等） | 多 worker/热加载进程、或 `AGENT_HARNESS_MODE` 在两个模块 import 之间被 env/DB 修改 | 常量集合不一致（如 router 用 zh、tool description 覆盖用 en） |
| B3 | team_agent/prompt.py:80-90 的 `SANDBOX_SYSTEM_PROMPT`/`SANDBOX_RUNTIME_SECTION` 无人 import（grep 确认仅 nodes.py:40-46 导入 5 个函数、__init__.py:3 导入 build_team_members_description） | 实现者只改 team_agent/prompt.py 的沙箱常量 | **改错地方**：真正注入的沙箱文本来自 search_agent/prompt.py（nodes.py:33-37），改动无运行时效果；且该死代码是 legacy 文本副本，容易误以为是生效路径 |
| B4 | tests/unit/agents/test_team_router.py:60（`"Capability summary: ..."`）、:86（`"## Team Instructions"`）、:110-111（`"Do not dispatch onboarding, coordination, reminder, or notification messages"`、`"The \`task\` tool is for work assignments only"`）断言英文 | 默认 `.env:243` = compact_zh 下这些字符串**不存在**于运行时常量（成员标签是 成员/能力/指令，标题是 ## 团队指令，router 是中文版） | 测试在默认模式下**应为红**（本地无 venv 依赖，未实际运行验证；dev/CI 可能以 legacy 模式跑测试，或这些测试当前已红）。hardcode zh 后必须同步改断言为中文等价物，否则持续红 |
| B5 | sop/prompt_section.py:53-63 的 `mode` 参数 + tests/agents/test_sop_prompt_section.py:36-40（`monkeypatch.setattr(prompt_section, "get_active_harness_mode", lambda: "legacy")`，断言 `"SOP Planning" in text`） | 删除 legacy 后该测试的 monkeypatch 分支失效 | 必须删除 `mode` 参数（或忽略之）并更新测试；否则遗留英文模板或测试红 |
| B6 | prompt.py:100,104,107,128 的 `if _HARNESS_MODE == "compact_zh" else <英文>` —— else 分支**同时覆盖 legacy 和 compact_en** | 无（当前行为正确） | 设计如此：compact_en/legacy 均走英文。删除后硬编码中文即等价。**风险仅在于**：若有人误删 `_HARNESS_MODE` 但没删全 4 处，会出现 `if <未定义>` → NameError 或误用 |

---

## ⑦ 删除建议（每处 legacy/compact_en 分支的删法，标注 hardcode zh 等价改法）

### src/agents/team_agent/prompt.py
| 位置 | 现在 | 改法 |
|---|---|---|
| :5 | `from ...harness_prompt_overrides import get_active_harness_mode, select_harness_text` | 改为 `from ...harness_prompt_overrides import select_harness_text`（若 :41 也删）或整行删除 |
| :7 | `_HARNESS_MODE = get_active_harness_mode()` | **删除**；不再需要 |
| :9-39 | `_LEGACY_TEAM_ROUTER_SYSTEM_PROMPT` | **删除** |
| :41-59 | `TEAM_ROUTER_SYSTEM_PROMPT = select_harness_text(legacy=..., compact_en=..., compact_zh=...)` | 替换为 `TEAM_ROUTER_SYSTEM_PROMPT = """<①中 zh 逐字文本>"""`（保留 3 个 format 占位符） |
| :61-79 | `_LEGACY_SANDBOX_SYSTEM_PROMPT` / `_LEGACY_SANDBOX_RUNTIME_SECTION` | **删除** |
| :80-90 | `SANDBOX_SYSTEM_PROMPT` / `SANDBOX_RUNTIME_SECTION` 两个 select_harness_text | **整块删除**（死代码，nodes.py 用的是 search_agent 版；保留反而误导）。若坚持保留则 hardcode ①中 zh 文本 |
| :100 | `member_label = "成员" if _HARNESS_MODE == "compact_zh" else "member_id"` | `member_label = "成员"` |
| :104 | `label = "能力" if _HARNESS_MODE == "compact_zh" else "Capability summary"` | `label = "能力"` |
| :107 | `label = "指令" if ... else "Instructions"` | `label = "指令"` |
| :128 | `heading = "## 团队指令" if _HARNESS_MODE == "compact_zh" else "## Team Instructions"` | `heading = "## 团队指令"` |

### src/agents/team_agent/sop/prompt_section.py
| 位置 | 现在 | 改法 |
|---|---|---|
| :10 | `from src.kernel.config import HarnessMode, get_active_harness_mode, settings` | 改为 `from src.kernel.config import settings` |
| :13-32 | `_LEGACY_SOP_GUIDANCE_SECTION` | **删除** |
| :35-42 | `_COMPACT_EN_SOP_GUIDANCE_SECTION` | **删除** |
| :45-49 | `_COMPACT_ZH_SOP_GUIDANCE_SECTION` | 保留（可内联进函数） |
| :53-63 | `def build_sop_guidance_section(mode: HarnessMode | None = None)` 三分支 + `mode or get_active_harness_mode()` | 删除 `mode` 参数；函数体直接 `return _COMPACT_ZH_...format(max_steps=..., min_steps=...)`（**保留 settings.TEAM_SOP_* 的 format**，zh 模板含 `[{min_steps}, {max_steps}]`） |

### 依赖注入链（team_agent 之外，需主 agent 界定删除边界）
- **若删除只限 team_agent**：`build_role_subagent_section`（subagent_prompts.py:422-460）的 5 处 `select_harness_text` 属共享代码（fast/search/team 共用），改 hardcode zh 会影响 fast/search 的子代理段——需确认是否接受；否则 team 路径会在"成员段=调用时 zh（默认模式）"下保持不变，无需动。
- **若全仓 hardcode zh**，还需处理：subagent_prompts.py（模块级 4 组 GUIDE + SUBAGENT_PROMPT + build_role_subagent_section）、fast_agent/prompt.py:21、search_agent/prompt.py:41/49/59、harness_prompt_overrides.py（`select_harness_text` 本体、catalog 的 en 分支、`build_short_todo_middleware`/`build_harness_extra_middleware` 的 legacy 分支）、persona.py:32,48,103-127、deferred_manager.py:20,23-48,235-242。
- **settings 层（决策项）**：`HarnessMode` 字面量（base.py:31）、`VALID_HARNESS_MODES`（base.py:33-38）、field validator（base.py:466）、`AGENT_HARNESS_MODE` 定义 options（definitions.py:208-212）、`RESTART_REQUIRED_SETTINGS`（constants.py:30）。若彻底删 legacy/compact_en 需收窄 Literal 并更新 .env（.env:243）与 spec（.trellis/spec/backend/agent-harness.md 全文按三模式书写）。

### 测试（删除后必改）
- `tests/unit/agents/test_team_router.py`：:60 改 `"能力: Investigates sources and verifies claims." in desc`；:86 改 `"## 团队指令"`；:110-111 改 zh 等价断言（如 `"不发送协调/提醒消息"` 与 `"用 \`task\` 分派"`，对应 zh router 文本措辞）。其余断言（slug、display_names、fallback、resolve_runtime_team）与 harness 无关。
- `tests/agents/test_sop_prompt_section.py`：:36-40 `test_default_mode_follows_active_harness_mode` 删除或改为直接断言 zh 模板；:11-21 `test_legacy_section_contains_key_constraints` **删除**。
- harness 宽域测试（若全仓删除才涉及）：`tests/agents/core/test_harness_prompt_overrides.py`（:51 参数化 compact_en/compact_zh、:255 legacy==()、:332-349 三模式子进程）、`tests/agents/core/test_subagent_prompts.py`（:196 三模式参数化）、`tests/kernel/config/test_sandbox_image_description_setting.py:36-38`（options 断言）。

---

## Caveats / Not Found

- **无法运行测试验证**：本地 `python` 无 venv 依赖（`langchain_core` ModuleNotFoundError），B4 的"测试当前红/绿"状态未实证，仅基于代码与 `.env:243` 推断。
- `src/agents/team_agent/orchestration.py` 不存在源码（仅 `__pycache__/orchestration.cpython-313.pyc` 残留）；`tests/agents/test_team_agent_foundations.py` / `test_team_agent_orchestration.py` 也仅剩 pyc——均为已删除模块的陈旧缓存，非本次范围。
- `build_team_member_system_prompt` 函数不存在（任务假设名）；实际成员构建见 ②。
- `TEAM_SOP_MODE` 相关（nodes.py:345, 535）与 harness 无关，未展开。
- deepagents 内部 HarnessProfile（persona.py:109-128）注册的 write_todos/task 工具描述本地化属 harness_prompt_overrides 层，已列于 B2/B⑦ 但不属 team_agent 注入链主体。
