# Research: compact_zh harness 剩余英文残留全量清单

- **Query**: 除删除 legacy/compact_en 外，把 harness system prompt 表面所有剩余英文（工具描述、schema 字段、系统段、vendor 未替换段）改为浓缩中文，真正建成全中文 harness
- **Scope**: internal（src + deepagents 0.6.7 vendor wheel + uv.lock 版本核对 + 既有 research 交叉验证）
- **Date**: 2026-08-04
- **配套调研**: 本任务目录另有 `search-fast-agent-prompts.md`、`team-agent-prompt-construction.md`、`backend-core-mechanism.md`、`frontend-i18n-tests-docs.md`；本文与其无冲突，聚焦「仍为英文的模型可见表面」。
- **方法说明**：deepagents 未装进当前解释器，为核对 vendor 侧，已临时下载 `deepagents==0.6.7` wheel 到 `%TEMP%\da-pkg` 只读核对其 system prompt 常量；未做任何代码修改。

---

## ① catalog 当前覆盖工具清单（harness_prompt_overrides.py）

`_ZH_TOOLS`（src/agents/core/harness_prompt_overrides.py:87-99）与 `_ZH_FIELDS`（111-124）覆盖 **10 个工具**。`localize_tool_for_model`（229-244）按 `tool.name ∈ catalog.schema_fields` 复制模型视图；`_replacements`（250-264）只替换 4 个 vendor 哨兵串。

| 工具 | `_ZH_TOOLS` 行号 | `_ZH_FIELDS` 行号 | 模型视图语言 |
|---|---|---|---|
| `task` | 87 | 123 | 中文（描述保留 `{available_agents}` 占位，渲染时展开；见 ④） |
| `ls` | 88 | 112 | 中文 |
| `read_file` | 89 | 113 | 中文 |
| `write_file` | 90 | 114 | 中文 |
| `edit_file` | 91 | 115 | 中文 |
| `glob` | 92 | 116 | 中文 |
| `grep` | 93 | 117 | 中文 |
| `execute` | 94 | 118 | 中文 |
| `search_tools` | 95 | 119 | 中文（`ToolSearchTool` 自身英文 description 在 tool_search_tool.py:55-69，被 catalog 覆盖） |
| `write_todos` | 96 | 120 | 中文 |

另有 `behavior_guide`（133）、`write_todos_system`（135-141）、`memory_guide`（145-151）、`filesystem_system`（159-163）、`execute_system`（165-168）、`task_system`（169-173）、`available_agents_heading`（174-176）均已本地化。

---

## ② 未覆盖 catalog 的自定义工具（模型可见 description 仍为英文）

以下工具不在 `_ZH_TOOLS`/`_ZH_FIELDS`，`localize_tool_for_model` 直接跳过，LLM 看到的是工具定义处的原始 description（`Annotated` 字段说明 + 函数 docstring 拼接）。长度均为估算字符数（含字段说明），**工具名/参数名是机器契约不可译**，此处只译描述文本。

| 工具 | 定义位置 | description 语言 | 估算长度 | 契约标识 | 翻译建议（浓缩中文） |
|---|---|---|---|---|---|
| `memory_retain` | src/infra/memory/tools.py:150 | 英文 docstring + 6 个英文字段（content/title/summary/context/tags/existing_memory_id） | ~330 | 名称/参数名不可译 | 「存储跨会话记忆。仅收高价值非临时信息；过短、似提问、像代码或重复近期记忆会被拒。优先存用户偏好、项目约束、反馈、外部链接，用 user_identity/project_constraint/feedback_rule/reference_link 等显式标签。」 |
| `memory_recall` | src/infra/memory/tools.py:208 | 英文 docstring + 3 字段（query/max_results/memory_types） | ~200 | 同上 | 「按语义检索跨会话记忆；返回与查询概念相关的历史记录。」 |
| `memory_delete` | src/infra/memory/tools.py:240 | 英文 docstring + 1 字段（memory_id） | ~140 | 同上 | 「按 ID 删除记忆；ID 取 memory_recall 输出。」 |
| `find_skills` | src/infra/tool/skill_marketplace_tool.py:118 | 英文 docstring + 2 字段（query/tags） | ~120 | 名称不可译 | 「当前工具无法完成任务时，搜索技能市场（按名称/描述/标签关键词）。」 |
| `install_skill` | src/infra/tool/skill_marketplace_tool.py:149 | 英文 docstring + 1 字段（name） | ~120 | 名称不可译 | 「将技能市场技能临时装入当前沙箱工作区；返回路径，读 SKILL.md 后按其脚本执行。」 |
| `dify_kb_retrieve` | src/infra/tool/dify_kb_tool.py:446 | 英文 docstring + 3 字段（query/top_k/score_threshold） | ~450 | 名称不可译 | 「从当前 persona 绑定的 Dify 知识库检索片段：LLM 改写查询并判定是否检索，并行搜索、去重、重排后返回 top 片段。」 |
| `read_document` | src/infra/tool/read_document_tool.py:256 | 英文 docstring + 1 字段（url） | ~330 | 名称不可译 | 「下载附件文档并返回文本：pdf/docx/pptx 走 MinerU 转 Markdown；txt/md/log/json/py 直接解码；xlsx/csv 不转文本，返回沙箱处理指引。」 |
| `audio_transcribe` | src/infra/tool/audio_transcribe_tool.py:117 | 英文 docstring + 5 字段（url/model/language/prompt） | ~120 | 名称不可译 | 「按 URL 下载音频并转写为文本。」 |
| `create_persona_preset` | src/infra/tool/persona_preset_tool.py:77 | 英文 docstring + 6 字段（name/system_prompt/description/avatar/tags/…） | ~200 | 名称不可译 | 「创建 persona 预设：system_prompt 需覆盖角色身份、行为准则、输出格式、约束四部分。」 |
| `update_persona_preset` | src/infra/tool/persona_preset_tool.py:164 | 英文 docstring + 多字段 | ~160 | 同上 | 「按 preset_id 或名称更新 persona 预设。」 |
| `search_persona_presets` | src/infra/tool/team_tool.py:98 | 英文 docstring + 2 字段（query/tag） | ~180 | 同上 | 「建团队前先按任务角色词检索 persona 预设；空串列出近期可见预设。」 |
| `create_agent_team` | src/infra/tool/team_tool.py:168 | 英文 docstring + 多字段（name/members/…） | ~250 | 同上 | 「按 search_persona_presets 结果组建团队；members 每项含 persona_preset_id 等字段。」 |
| `env_var_list` | src/infra/tool/env_var_tool.py:68 | 英文 docstring | ~100 | 同上 | 「列出当前用户已保存的环境变量名（值恒为掩码，绝不回显明文）。」 |
| `env_var_set` | src/infra/tool/env_var_tool.py:86 | 英文 docstring + 字段 | ~120 | 同上 | 「保存环境变量（密文存储，不回读明文）。」 |
| `env_var_delete` | src/infra/tool/env_var_tool.py:118 | 英文 docstring | ~90 | 同上 | 「删除指定环境变量。」 |
| `env_var_delete_all` | src/infra/tool/env_var_tool.py:145 | 英文 docstring | ~90 | 同上 | 「删除全部环境变量。」 |
| `sandbox_mcp_add` | src/infra/tool/sandbox_mcp_tool.py:121 | 英文 docstring + 3 字段（server_name/command/env_keys） | ~200 | 同上 | 「注册沙箱 MCP 服务器并持久化；env_keys 为注入的环境变量 KEY 名列表。」 |
| `sandbox_mcp_update` | src/infra/tool/sandbox_mcp_tool.py:176 | 英文 docstring + 3 字段 | ~190 | 同上 | 「更新沙箱 MCP 服务器的命令或环境变量注入。」 |
| `sandbox_mcp_remove` | src/infra/tool/sandbox_mcp_tool.py:258 | 英文 docstring + 1 字段 | ~120 | 同上 | 「移除沙箱 MCP 服务器并删除数据库记录。」 |
| `image_generate` | src/infra/tool/image_generation_tool.py:559 | 英文 docstring + 4 字段（prompt/input_images/background/…） | ~180 | 同上 | 「生成或编辑图片；给 input_images 即图生图模式。」 |
| `upload_url_to_sandbox` | src/infra/tool/upload_url_tool.py:97 | **docstring 英文**（~130），字段（url/file_path）已中文 | ~130 | 同上 | docstring 改「从 URL 下载文件到沙箱文件系统，供 shell/脚本访问。」 |

### 已本地化、无需处理（核验通过）

| 工具 | 位置 | 状态 |
|---|---|---|
| `ask_human` | src/infra/tool/human_tool/tool.py:24-95（description）+ models.py:100-123（AskHumanInput 字段） | 全中文 ✓ |
| `transfer_file` / `transfer_path` | src/infra/tool/transfer_file_tool.py:324 / 489 | 全中文 ✓ |
| `reveal_file` / `reveal_project` | src/infra/tool/reveal_file_tool.py:525 / reveal_project_tool.py:485 | 全中文 ✓ |
| `update_sop` | src/agents/team_agent/sop/tool.py:262-280 | 全中文 ✓ |
| `search_tools`（自身） | src/infra/tool/tool_search_tool.py:47-69 | 英文但被 catalog 覆盖（compact_zh 模型视图为中文）✓ |

### 内部工具（不进主 harness 表面，仅备注）

- `memory_compaction_update` / `memory_compaction_delete`（src/infra/memory/compaction_agent.py:433/475）：英文 docstring，但只被记忆压缩 agent 内部 graph 使用，不注入主代理/子代理工具列表。
- 第三方 MCP 工具（mcp_client.py `MCPToolWithRetry` 等）：描述来自 MCP server，**按契约透传不改**（result.md 已声明），不属于一方法化范围。

---

## ③ 系统 prompt 英文残留段落清单

| # | 位置 | 当前英文（要点） | 触发条件 | 翻译建议 |
|---|---|---|---|---|
| 1 | src/infra/skill/loader.py:124-135 `build_skills_prompt` | `## Skills System` + **Skills Location**: `/skills/` + **Available Skills:** + **Usage:** + **Commands:** + **IMPORTANT:**（~700 字符） | 所有 agent，`ENABLE_SKILLS` 且用户有技能 | `## 技能`：`/skills/` 是数据库虚拟路径，仅用 ls/read_file/write_file/edit_file 访问，禁止 shell 直读；脚本先 transfer_file/transfer_path 到工作区再执行。可并入 `_ZH_TOOLS` 思路做成 `select_harness_text` 三态 |
| 2 | src/infra/persona_preset/skill_harness.py:44-54 `build_persona_skill_harness_section` | `## Persona skill capabilities` + "The active Persona recommends the exact Marketplace skills below. When the user's task matches one of these descriptions, call `install_skill` with that exact skill name… Do not call `find_skills` first…" | persona 带 skill hints 且 install_skill 可用（沙箱） | `## Persona 技能能力`：命中描述即用 `install_skill` 安装精确技能名，勿先 `find_skills`；只装相关技能，按返回路径读 SKILL.md |
| 3 | src/infra/tool/skill_marketplace_tool.py:39-45 `_MARKETPLACE_SKILL_PROMPT` | `## Marketplace skills` + "If the task needs a capability you do not currently have, use `find_skills` before giving up. Then use `install_skill`…" | 沙箱 agent（team/search 沙箱路径） | `## 技能市场`：缺能力先 `find_skills` 再 `install_skill` 装入选定技能；直接读返回的 SKILL.md 运行脚本，无需 transfer |
| 4 | src/infra/goal.py:46-55 `build_goal_prompt_section` | `## Active Goal` + "Objective: … Completion rubric: … Work toward this goal across turns…" | 激活 active_goal 时 | `## 当前目标`：目标/评分细则 + 逐轮推进直至 rubric 满足，每条显式要求须对照当前证据 |
| 5 | src/infra/tool/env_var_prompt.py:44-51 | `## Available Environment Variables` + "The following environment variables are configured… Use the names directly in shell commands or code… Do not print or reveal secrets." | 沙箱 agent 且用户有环境变量 | `## 可用环境变量`：仅列键名（值不显示），shell/代码直接引用 `$KEY`，禁止打印或泄露 |
| 6 | src/infra/tool/sandbox_mcp_prompt.py:245-260（intro_lines）+ 304-306 服务器分组头 | `## Sandbox Tools (NOT MCP — DO NOT call directly)` + ⚠️ IMPORTANT 段 + "**How to use**… **Required first-use sequence**…"（~700 字符） | 沙箱且 mcporter 有可用工具 | `## 沙箱工具（非 MCP，禁止直接调用）`：必须经 `execute` + `mcporter` 调用；首次调用前先 `mcporter list` 定位、`mcporter list <service> --schema` 查看参数 |
| 7 | src/infra/tool/sandbox_mcp_prompt.py:128-130 / 143-146 `_maybe_append_overflow_hint(_sections)` | `> **Note:** Only {N} of {M} tools are shown above. Use `execute(command="mcporter list")`…` | 工具数超 `_MAX_TOOLS_IN_PROMPT` | `> 注：仅显示 {N}/{M} 个工具；用 mcporter list / list <service> --schema 查找` |
| 8 | src/infra/tool/deferred_manager.py:232 | 注入段头 `## MCP Tools (Deferred)`（三态共用英文头） | 有未发现延迟 MCP 工具 | 可改 `## 延迟 MCP 工具`，但**必须同步**：prompt_caching.py:131 的 volatile 前缀判定 `"## mcp tools (deferred)"`、DEFERRED_TOOL_SEARCH_GUIDE 两处引用、测试断言。见 ⑥ |
| 9 | src/agents/fast_agent/nodes.py:199-201 | `"description": "General-purpose agent for researching complex questions, searching for files and content, and executing multi-step tasks. …"`（~400 字符） | fast 主代理 `task` 工具可用代理列表 + "Available subagent types:" 段 | `通用子代理：研究复杂问题、搜索文件内容、执行多步任务；关键词/文件首查无果时交其代查；工具权限与主代理一致` |
| 10 | src/agents/search_agent/nodes.py:248-250 | 同上 general-purpose 英文 description | search 主代理 | 同上 |
| 11 | src/agents/team_agent/nodes.py:463-469 | `"Team member '{role_name}' (member_id: {id}). Dispatch tasks matching this role's expertise."` + role_instructions 原样 | 团队模式 role subagent 列表 | `团队成员 {role_name}（member_id: {id}）：分派该角色专长任务。`（role_instructions 为管理员内容，透传） |
| 12 | src/infra/memory/client/native/indexing.py:98-101 | `<memory_index>` 内 `## [user/feedback/project/reference]` 分节标签 | ENABLE_MEMORY + NATIVE_MEMORY_INDEX_ENABLED | 标签值即 memory_type 枚举（数据契约），建议保留；可后续做展示层映射 |
| 13 | src/infra/memory/client/types.py:107-119 `NATIVE_MEMORY_GUIDE` | 全英文跨会话记忆指南 | **仅 legacy**；compact 走 catalog.memory_guide（中文） | 无需处理（compact_zh 不触发） |
| 14 | src/infra/agent/middleware/prompt_caching.py:131 | volatile 前缀 `"## mcp tools (deferred)"`、`"## user runtime context"` 等 | 缓存判定逻辑，非模型可见文本 | 仅当 #8 改头时同步改 |

### 已确认中文、无残留（compact_zh 分支逐段核验）

- `src/agents/core/subagent_prompts.py`：`_ZH_FILE_WORKSPACE_GUIDE`（127-131）、`_ZH_FILE_REVEAL_GUIDE`（133-139）、`_ZH_SAFETY_GUIDE`（141-159）、`_ZH_TOOL_DISCOVERY_GUIDE`（161-169）、`_ZH_SUBAGENT_TASK_GUIDE`（265-271）、`_build_zh_subagent_prompt`（379-393）——均中文，仅含工具名/契约标识。
- `src/agents/core/persona.py`：`_BEHAVIOR_GUIDE`（101-103）compact_zh 走 `COMPACT_ZH_BEHAVIOR_GUIDE`（中文）；`DEFAULT_ROLE`（48）中文。
- `src/agents/fast_agent/prompt.py` / `search_agent/prompt.py` / `team_agent/prompt.py` / `team_agent/sop/prompt_section.py`：全部 `select_harness_text` 三态，compact_zh 分支中文。
- `src/infra/tool/deferred_manager.py`：`DEFERRED_TOOL_SEARCH_GUIDE` compact_zh（35-39）中文；hidden-count 文案（244-247）中文。
- `src/agents/core/harness_prompt_overrides.py`：`COMPACT_ZH_BEHAVIOR_GUIDE`（42-51）、catalog 各段（135-176）中文。

---

## ④ vendor（deepagents 0.6.7）未替换英文段

`_replacements`（harness_prompt_overrides.py:250-264）现替换 4 个哨兵串：`FILESYSTEM_SYSTEM_PROMPT`、`EXECUTION_SYSTEM_PROMPT`、`TASK_SYSTEM_PROMPT`、`"Available subagent types:"`。

对 0.6.7 wheel 源码逐文件核对（`%TEMP%\da-pkg\x\deepagents`）：

| vendor 项 | 状态 |
|---|---|
| `BASE_AGENT_PROMPT`（graph.py:69+，英文） | **无泄漏**：compact 模式由 HarnessProfile `base_system_prompt` = `COMPACT_ZH_BEHAVIOR_GUIDE` 整体替换（persona.py:101-103；harness_profiles.py:256-267） |
| `_FILESYSTEM_SYSTEM_PROMPT_TEMPLATE`（filesystem.py:447-467，含 `## Following Conventions`） | 被 `FILESYSTEM_SYSTEM_PROMPT` 整串替换 ✓；注意替换是**精确字符串匹配**，默认 `large_tool_results_prefix="/large_tool_results"` 才命中——全仓无自定义前缀（grep 0 命中），安全但属脆弱点 |
| `EXECUTION_SYSTEM_PROMPT`（filesystem.py:472-477） | 已替换 ✓ |
| `TASK_SYSTEM_PROMPT`（subagents.py:390+） | 已替换 ✓ |
| `"Available subagent types:"`（subagents.py:707） | 已替换 ✓ |
| `TASK_TOOL_DESCRIPTION`（subagents.py:280+，含 `<example_agent_descriptions>` 英文示例） | 不生效：catalog 的 `_ZH_TOOLS["task"]` 经 `tool_description_overrides` 覆盖后，`SubAgentMiddleware` 只对 `{available_agents}` 占位做 `.format()`（subagents.py:489-490） |
| TodoListMiddleware（langchain）write_todos | compact 模式 excluded + `build_short_todo_middleware` 注入中文版 ✓（persona.py:119-123） |
| `SkillsMiddleware`（skills.py） | LambChat 传 `skills=None` 禁用 ✓；替代的 `build_skills_prompt` 即本文 ③#1（英文，需本地化） |
| `MemoryMiddleware` / `MEMORY_SYSTEM_PROMPT`（memory.py:104-168） | LambChat 未使用 ✓ |
| `GRADER_SYSTEM_PROMPT`（rubric.py:133-145） | **条件性英文残留**：仅 active_goal 激活时 RubricMiddleware 启用，grader 子代理收到英文评分 prompt；属内部子代理、非主 harness 表面，可标记为低优先 |
| `MEMORY_SYSTEM_PROMPT` 之外的 vendor 系统段 | 默认中间件栈仅上述几类，其余（patch_tool_calls/summarization/permissions/async_subagents）不注入系统提示或 LambChat 未启用 |

**结论：vendor 侧除 rubric 条件性段外，无 4 哨兵之外的常驻英文泄漏；③#9-11 的子代理 description 是一方代码（非 vendor）。**

---

## ⑤ 翻译优先级排序（影响面 × 实现成本）

| 优先级 | 目标 | 影响面 | 实现成本 | 建议做法 |
|---|---|---|---|---|
| P1 | ② `memory_recall/retain/delete` | 高：ENABLE_MEMORY 时所有 agent 常驻 3 个英文工具 | 低：照 `_ZH_TOOLS`/`_ZH_FIELDS` 模式在 catalog 加 3 键即可，无需改工具文件 | 加 `memory_recall`/`memory_retain`/`memory_delete` 到 `_ZH_TOOLS`+`_ZH_FIELDS` |
| P2 | ③#1 `build_skills_prompt`（`## Skills System`） | 高：有技能用户每轮注入 | 中：单函数内改 `select_harness_text` 三态或直接中文 | 直接压缩中文，段落精简到 ~200 字符 |
| P3 | ③#9-11 子代理 description（general-purpose ×2、team member ×1） | 中高：`task` 工具可用代理列表每轮可见 | 低：3 处字符串 | 按模式本地化（fast/search 共用常量，team 模板格式化） |
| P4 | ② `read_document` / `dify_kb_retrieve` / `audio_transcribe` | 中：文档/知识库/音频场景 | 低：catalog 加键 或 工具内改中文 | 优先 catalog 加键（不动执行真值） |
| P5 | ② `env_var_*`(4) / `sandbox_mcp_*`(3) / `upload_url_to_sandbox` docstring / `image_generate` | 中：沙箱 agent | 低-中：8 个工具 | catalog 加键或工具内改中文 |
| P6 | ③#2 Persona skill harness、③#3 `## Marketplace skills` | 中低：persona hints + 沙箱才触发 | 低 | 改固定英文为中文 |
| P7 | ② `create/update_persona_preset` / `search_persona_presets` / `create_agent_team` | 低-中：团队/persona 管理场景 | 低 | catalog 加键 |
| P8 | ③#6-7 沙箱工具提示段 + overflow 提示 | 低-中：沙箱且工具多时 | 中：段落较长 | 压缩中文 |
| P9 | ③#4 `## Active Goal` | 低：仅 goal 激活 | 低 | 中文化 |
| P10 | ③#8 `## MCP Tools (Deferred)` 头 | 低：仅一个标题 | 中：需联动 prompt_caching.py:131 + 两处 guide 引用 + 测试 | 若要改，全链路同步；否则保留（视为契约） |
| P11 | ④ rubric `GRADER_SYSTEM_PROMPT`、③#12 memory index 分节标签 | 极低：内部子代理/数据枚举 | 高/不必要 | 建议暂不处理 |

**建议 P1-P3 为本任务核心交付；P4-P7 属低成本顺带；P8-P11 可留 follow-up。**

---

## ⑥ 不可翻译的机器契约标识（必须保留英文）

翻译描述文本时不得改动以下标识；改动会破坏工具调用、schema 校验、缓存断点或子代理交接契约：

**工具名**：`task`、`ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`、`execute`、`search_tools`、`write_todos`、`memory_recall/retain/delete`、`ask_human`、`reveal_file`、`reveal_project`、`transfer_file`、`transfer_path`、`upload_url_to_sandbox`、`find_skills`、`install_skill`、`update_sop`、`dify_kb_retrieve`、`read_document`、`audio_transcribe`、`env_var_list/set/delete/delete_all`、`sandbox_mcp_add/update/remove`、`image_generate`、`create/update_persona_preset`、`search_persona_presets`、`create_agent_team`、`memory_compaction_update/delete`。

**参数名**：`file_path`、`path`、`pattern`、`offset`、`limit`、`content`、`old_string`、`new_string`、`replace_all`、`output_mode`、`command`、`timeout`、`query`、`subagent_type`、`todos`、`status`、`url`、`message`、`fields`、`source_path`、`target_path`、`source_dir`、`target_prefix`、`plan`、`action`、`direct_answer`、`step_id`、`assignee`、`expected_output`、`preset_id`、`member_id`、`persona_preset_id`、`server_name`、`env_keys`、`name`、`title`、`summary`、`context`、`tags`、`memory_id`、`max_results`、`memory_types`、`existing_memory_id`、`top_k`、`score_threshold`、`input_images`、`background`、`template`、`project_path`、`description` 等。

**枚举/状态值**：`in_progress`/`completed`、`pending`/`running`/`succeeded`/`failed`/`cancelled`、`files_with_matches`/`content`/`count`、`project`/`folder`、`text`/`textarea`/`number`/`checkbox`/`select`/`multi_select`、`auto`/`opaque`/`transparent`、`development`/`staging`/`production`、`create`/`update`、`direct_answer=true`、`action=create`、`server:tool`、`select:server:tool`。

**交接/日志锚点**：`## Handoff Notes` + 10 个英文字段标签（`Goal`、`What I checked`、`Key findings`、`Files / tools touched`、`Decisions or assumptions`、`Risks / blockers`、`Checks run`、`Unchecked items`、`Suggested next step`、`Memory-worthy notes`）——`_ZH_HANDOFF` 已明确标注"字段标签是稳定交接契约"；`Current task start time: YYYY-MM-DD HH:mm:ss ±HH:MM Timezone`（`_ZH_SUBAGENT_TASK_GUIDE` 与 `_ZH_TOOLS["task"]` 均保留，且注释明确要求英文原样）；`[Activity log saved to: ...]`。

**系统锚点/路径**：`## MCP Tools (Deferred)`（prompt_caching.py:131 volatile 前缀判定 + `DEFERRED_TOOL_SEARCH_GUIDE` 两处引用；改则三处联动）；`<memory_index>`、`/large_tool_results/<tool_call_id>`、`/skills/`、`SKILL.md`、`/memories/`、`mode: "project"`/`mode: "folder"`、`mcporter`、`work_dir`；memory_type 值 `user`/`feedback`/`project`/`reference`。

---

## Caveats / Not Found

- **未发现**：vendor 默认栈在 compact_zh 下除 rubric 条件段外的其他常驻英文系统段；`_ZH_*` 各 guide 中混入的英文整句（均只剩工具名/契约锚点）。
- **脆弱点**：`_replacements` 是精确字符串替换——若未来给 `FilesystemMiddleware` 传自定义 `large_tool_results_prefix`，`FILESYSTEM_SYSTEM_PROMPT` 替换会静默失效、英文模板整段回漏。建议在 `_replacements` 改为基于模板常量（`_FILESYSTEM_SYSTEM_PROMPT_TEMPLATE`）正则或分段替换，或加一个替换失败哨兵检测。
- **外部数据**：MCP 工具描述、role_instructions、persona system_prompt、skill description、`SANDBOX_IMAGE_DESCRIPTION` 等属用户/管理员内容，透传不改（③#11 与 #12 属此类边界）。
- **deepagents wheel 仅供只读核对**，未改动项目任何依赖。
