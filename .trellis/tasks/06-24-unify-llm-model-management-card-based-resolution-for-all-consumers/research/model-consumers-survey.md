# 全部 LLM 消费方盘点

## 核心结论

- `LLMClient.get_model` 已是所有聊天 LLM 唯一构造入口，生产代码零绕过（`ChatOpenAI(`/`ChatAnthropic(` 只出现在 `client.py:_create_model` 内部）。
- 但「配置来源」割裂：10 个 get_model 调用点，1 个用卡片 ID，3 个用已解析卡片，6 个传裸串+独立 api_base/api_key，散在 5 套互不相关 settings 字段。
- 转写/embedding/rerank 完全不走 LLMClient 也不走卡片，各自 httpx/AsyncOpenAI 直连独立 API。
- `get_langgraph_model`(`client.py:550-555`) 死代码。

## 消费方总览表

| # | 消费方 | 配置项 | 引用方式 | 调用点 | 取模型方式 |
|---|---|---|---|---|---|
| 1 | Fast 主对话 | DEFAULT_MODEL_ID(经agent_options) | 卡片ID+已解析卡片 | fast_agent/nodes.py:90 | model=,model_id=,model_config= |
| 2 | Search 主对话 | 同上 | 同上 | search_agent/nodes.py:101 | 同 |
| 3 | Team 主对话 | 同上 | 同上 | team_agent/nodes.py:149 | 同 |
| 4 | 会话标题 | SESSION_TITLE_MODEL/+API_BASE/+API_KEY | 裸串三件套 | session.py:729 | model=,api_base=,api_key= |
| 5 | 推荐追问 | 复用SESSION_TITLE_* | 裸串三件套 | recommendations.py:489 | 同 |
| 6 | 记忆 retain/eval | NATIVE_MEMORY_MODEL/+API_BASE/+API_KEY/+MAX_TOKENS | 裸串三件套 | memory/client/native/backend.py:145 | model=,api_base=,api_key= |
| 7 | 记忆 compaction | NATIVE_MEMORY_COMPACTION_MODEL_ID | 卡片ID | compaction_agent.py:528 | model_id= |
| 8 | Subagent 活动压缩 | 无配置(落默认) | 隐式默认 | middleware_subagent.py:330 | 不传→get_default_model()→DEFAULT_MODEL_ID |
| 9 | Fallback | ModelConfig.fallback_model(卡片ID→解析成裸value) | 裸串(绕圈) | retry.py:187 | model=self._fallback_model |
| 10 | Goal/Rubric | 复用主对话已构造llm | 不二次取 | goal.py create_goal_rubric_middleware | 直接传llm |

## 非聊天 LLM 服务（独立直连）

| 服务 | 构造 | 调用点 | 配置项 |
|---|---|---|---|
| 音频转写 | AsyncOpenAI→audio.transcriptions.create | audio_transcribe_tool.py:85-94,180 | AUDIO_TRANSCRIPTION_API_KEY/BASE_URL/MODEL |
| Embedding | httpx→POST /v1/embeddings | backend.py:540-555 | NATIVE_MEMORY_EMBEDDING_API_BASE/API_KEY/MODEL |
| Rerank | httpx→POST /v1/rerank | search.py:361-403 | NATIVE_MEMORY_RERANK_MODEL/API_BASE/API_KEY |

## 卡片引用 vs 裸串

已走卡片：主对话三节点、compaction、DEFAULT_MODEL_ID 本身。
裸串三件套：SESSION_TITLE_*(标题+推荐)、NATIVE_MEMORY_*(retain)、fallback(绕圈)、subagent(隐式)。
独立直连：转写、embedding、rerank。

## 统一模型管理现状评估

LLMClient 已具备工厂雏形（缓存/provider推断/卡片解析/权限/api_key回查）。还差：
1. 5 套裸串三件套待收编成 _ID。
2. 转写/embedding/rerank 三套独立客户端：决定只统一 chat 卡片，还是扩展卡片 kind 覆盖这三类。
3. subagent 压缩需明确配置位。
4. fallback 绕圈改直传 model_id。
5. 删 get_langgraph_model 死代码。

## 关键文件索引

- `src/infra/llm/client.py`（get_model 325-547, _create_model 217-322, 死代码 550-555）
- `src/kernel/config/definitions.py`（DEFAULT_MODEL_ID 30, SESSION_TITLE_* 281-302, AUDIO_TRANSCRIPTION_* 678-712）
- `src/kernel/config/_definitions_extra.py`（NATIVE_MEMORY_MODEL/COMPACTION_MODEL_ID 664-694, EMBEDDING_* 541-564, RERANK_* 586-608）
- `src/kernel/config/base.py`（默认值 287-327）
- `src/kernel/schemas/setting.py:29-55`（SettingCategory 枚举，6 个模型相关分类）
- `src/agents/core/node_utils.py:25-73`（resolve_fallback_model 绕圈点）
- `src/infra/tool/audio_transcribe_tool.py:85-94,180`
- `src/infra/memory/client/native/backend.py:145,527-561`
- `src/infra/memory/client/native/search.py:361-423`
- `src/infra/memory/compaction_agent.py:523-528`
