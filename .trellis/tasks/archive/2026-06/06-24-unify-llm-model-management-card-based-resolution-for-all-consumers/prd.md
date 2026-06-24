# 统一大模型管理：卡片化解析所有消费方

## Goal

把 LambChat 里散落各处的 LLM 配置收编到统一的「模型卡片」体系。所有消费方（主对话、会话标题、推荐追问、记忆 retain/eval、记忆 compaction、音频转写、embedding、rerank、fallback、subagent 压缩）都通过引用卡片 ID 来取模型，不再各自配 `model + api_base + api_key` 三件套。顺带修掉两个既有 bug：(1) 选了 OpenAI 兼容协议仍打 Anthropic `/v1/messages` 导致重试风暴；(2) 标题/推荐模型不能下拉选已配卡片。

## What I already know（来自 3 份调研，已落到 research/）

- `LLMClient.get_model` 已是聊天模型唯一构造入口，零绕过；具备 provider 推断、卡片解析、LRU 缓存、权限校验、api_key DB 回查。
- 10 个聊天消费方 + 3 个非聊天 LLM 服务现状（详见 `research/model-consumers-survey.md`）：
  - 已走卡片 ID：主对话三 agent、compaction。
  - 裸串三件套：`SESSION_TITLE_*`（标题+推荐共用）、`NATIVE_MEMORY_*`（retain/eval）。
  - 独立直连：转写（AsyncOpenAI）、embedding（httpx）、rerank（httpx）。
  - 绕圈：fallback 先按卡片查再退化成裸串传回 get_model。
  - 隐式：subagent 压缩无配置、落默认。
  - 死代码：`get_langgraph_model`。
- 协议判定根因（详见 `research/protocol-retry-rootcause.md`）：无独立 `protocol` 字段，靠 provider 派生；裸串路径丢 provider → 按模型名前缀硬猜（claude→anthropic）→ 协议错配 → SDK+应用层双层重试风暴。
- 生态边界（详见 `research/langchain-langgraph-model-mgmt.md`）：LangChain `init_chat_model` 给"构造层"，LangGraph `configurable` 给"注入层"；"管理层"（卡片/存储/加密/权限/下拉）留给应用层。业界共识 = registry/factory + 声明式引用（LobeChat、Open WebUI）。项目主对话流已是 LangGraph 官方 `customModelName` 同构模式。

## Assumptions (temporary)

- 旧裸串三件套保留为 fallback（空 ID 时走旧路径），不破坏已部署用户。
- 卡片 schema 加 `kind` 字段区分 chat/embedding/rerank/transcribe。
- 底层 provider→Chat 映射仍用项目现有 `_parse_provider`/`PROVIDER_REGISTRY`（不强制迁 `init_chat_model`，作为可选优化）。

## Open Questions（待 brainstorm 收口）

- ~~Q1: 旧三件套是否删除~~ → **已决策：本次直接删除**，但加一次性自动迁移保存量。
- ~~Q2: 协议错配快速失败严格度~~ → **已决策：硬失败抛错**（保守启发式，仅强信号矛盾抛 ValueError）。
- ~~Q3: subagent 压缩配置~~ → **已决策：继续隐式落 DEFAULT_MODEL_ID**，不新增配置项。

## Decision (ADR-lite) — Q1

**Context**: 旁路消费方各有裸串三件套，需收编成卡片 ID。
**Decision**: 本次直接删除旧三件套（设置项 + 后端读取 + UI），不留永久 fallback。但加一次性自动迁移：启动/refresh 时若旧三件套非空且对应新 `_MODEL_ID` 为空，按 value 反查卡片自动回填 `_ID`；找不到匹配卡片则 logger.warning 提示管理员手动建卡片并选择。
**Consequences**: 代码最干净，无双套并存。已部署用户配置经自动迁移保留；未匹配的需管理员手动处理（可接受，属一次性运维）。需写迁移逻辑 + 测试。

## Decision (ADR-lite) — Q2

**Context**: 裸串路径协议错配会触发 SDK 9 次重试风暴，需快速失败。
**Decision**: `_create_model` 在协议与 api_base 强信号矛盾时抛 `ValueError` 硬失败（如 api_base 路径含 `/v1/chat/completions`/`/openai` 却走 anthropic 协议，或反之）。启发式保守，仅强信号才抛，避免误报同时支持双路由的代理。
**Consequences**: 配错立即暴露、不再重试 9 次。极少数双路由代理可能被误报（可接受，用户改 api_base 或走卡片 provider 即可）。需写启发式 + 单测覆盖正/负例。

## Requirements (evolving)

### 阶段 1：标题/推荐卡片化 + 协议快速失败（解燃眉之急）
- 新增 `SESSION_TITLE_MODEL_ID`（STRING, category=SESSION, subcategory=title, default="", frontend_visible）。
- 标题生成（`session.py:722`）、推荐追问（`recommendations.py:489`）改用 `SESSION_TITLE_MODEL_ID` 走 `get_model(model_id=...)`。
- **删除** `SESSION_TITLE_MODEL`/`SESSION_TITLE_API_BASE`/`SESSION_TITLE_API_KEY` 设置项 + 后端读取 + 前端渲染。
- 一次性迁移：启动时旧三件套非空且新 ID 空 → 按 value 反查卡片回填；无匹配卡片则 warning。
- 前端 `MODEL_CONFIG_SETTING_KEYS` 加 `SESSION_TITLE_MODEL_ID` → 自动变下拉框选卡片。
- `LLMClient._create_model` 增加协议-api_base 错配快速失败（严格度按 Q2）。

### 阶段 2：旁路消费方收编
- 记忆 retain/eval：新增 `NATIVE_MEMORY_MODEL_ID`，**删除** `NATIVE_MEMORY_MODEL/API_BASE/API_KEY` 三件套 + 迁移。
- 卡片 schema 加 `kind` 字段（chat/embedding/rerank/transcribe）。
- 转写：新增 `AUDIO_TRANSCRIPTION_MODEL_ID` 引用 kind=transcribe 卡片，**删除** `AUDIO_TRANSCRIPTION_API_KEY/BASE_URL/MODEL` + 迁移（保留 ENABLE_AUDIO_TRANSCRIPTION 总开关）。
- embedding：新增 `NATIVE_MEMORY_EMBEDDING_MODEL_ID` 引用 kind=embedding 卡片，**删除**旧三件套 + 迁移。
- rerank：新增 `NATIVE_MEMORY_RERANK_MODEL_ID` 引用 kind=rerank 卡片，**删除**旧三件套 + 迁移（保留 local_rerank 回退）。
- 各自前端下拉。

### 阶段 3：清理
- fallback 路径改为直接传 `model_id`，去掉卡片→裸串绕圈（`node_utils.py:25` `resolve_fallback_model`）。
- 删死代码 `get_langgraph_model`。
- subagent 压缩：**不改**，继续隐式落 DEFAULT_MODEL_ID（Q3 决策）。
- 确认所有旧三件套已无引用后移除定义。

## Acceptance Criteria (evolving)

- [ ] 标题/推荐可在设置页下拉选已配模型卡片，旧裸串三件套设置项已删除。
- [ ] 选中 OpenAI 兼容卡片后，标题/推荐不再打 `/v1/messages`，无重试风暴。
- [ ] 协议错配场景 `_create_model` 抛 ValueError 硬失败（强信号矛盾），不触发 SDK 9 次重试；正常双路由代理不误报。
- [ ] 记忆 retain/转写/embedding/rerank 均可下拉选卡片，旧三件套已删除。
- [ ] 卡片含 `kind` 字段（chat/embedding/rerank/transcribe），下拉按 kind 过滤选项。
- [ ] fallback 走 model_id 直传，无绕圈。
- [ ] 一次性迁移：旧三件套非空 → 按 value 反查卡片回填 _ID；无匹配卡片则 warning。
- [ ] subagent 压缩继续隐式落 DEFAULT_MODEL_ID（行为不变）。
- [ ] `get_langgraph_model` 死代码已删。
- [ ] 全部既有测试通过 + 新增覆盖（卡片化解析、协议快速失败正/负例、迁移逻辑）。
- [ ] 前端 i18n 文案齐全，前端构建绿，lint/typecheck 绿。

## Definition of Done

- 测试新增/更新（单测 + 集成），uv run pytest 绿。
- lint/typecheck 绿。
- 前端构建绿。
- 行为变更同步前端 i18n 文案。
- 旧配置迁移/回退路径明确，rollout/rollback 已考虑。

## Out of Scope (explicit)

- 不强制把底层 `_parse_provider` 迁到 LangChain `init_chat_model`（可选优化，另立任务）。
- 不重构卡片管理 UI 本身（只加下拉引用，不动卡片增删改）。
- 不改 LangGraph/deepagents 编排结构。

## Research References

- [`research/protocol-retry-rootcause.md`](research/protocol-retry-rootcause.md) — 协议判定与重试风暴根因（断点①②③）
- [`research/model-card-dropdown-feasibility.md`](research/model-card-dropdown-feasibility.md) — 模型卡片管理现状 + 下拉框可行性 + 改动点清单
- [`research/model-consumers-survey.md`](research/model-consumers-survey.md) — 全部消费方盘点表
- [`research/langchain-langgraph-model-mgmt.md`](research/langchain-langgraph-model-mgmt.md) — 生态实践与借鉴边界

## Technical Notes

- 改造模板：`compaction_agent.py:523-528`（`get_model(model_id=...)` 现成范式）。
- 下拉范式：`DEFAULT_MODEL_ID` / `NATIVE_MEMORY_COMPACTION_MODEL_ID`（`MODEL_CONFIG_SETTING_KEYS` + `SettingsPanel.tsx:753-783`）。
- 协议判定核心：`client.py:68-95`（`_resolve_protocol`/`_parse_provider`/`PROVIDER_REGISTRY`），`_create_model:217-322`。
- 配置登记：`definitions.py`、`base.py`、`service.py`（`_ALLOW_EMPTY_STRING_SETTINGS`、`llm_affected_settings`）。
- 前端下拉触发：`SettingsPanel.constants.ts:41-44` `MODEL_CONFIG_SETTING_KEYS`。
