# LangChain/LangGraph 生态模型管理实践与借鉴边界

## 生态能给什么（直接用）

| 能力 | 用什么 | 来源 |
|---|---|---|
| 跨 provider 统一构造 Chat | `init_chat_model("provider:model", **kwargs)` 自动 provider→集成包 | reference.langchain.com |
| OpenAI 兼容端点 | `init_chat_model` 传 base_url/api_key | open-canvas getModelFromConfig |
| 运行时按 model_id 动态切（主对话流） | `RunnableConfig.configurable["model_id"]` → 节点内构造（LambChat 已是此模式） | LangGraph Configuration how-to / open-canvas utils.ts |
| deepagents 注入 | 传已构造 BaseChatModel 给 create_deep_agent(model=...) | docs.langchain.com/deepagents/models |
| 新写法（迁 create_agent） | context_schema + @wrap_model_call 中间件 init_chat_model(runtime.context.model) | deepagents/models |
| provider 级默认 init 参数 | register_provider_profile | deepagents/profiles |

## 项目必须自建（生态不提供）

1. 模型卡片 schema/持久化（参考 LobeChat ChatModelCard：id/displayName/provider/contextWindowTokens/能力位 functionCall/vision/reasoning + ModelProviderCard：api_base/api_key/settings）。
2. 卡片目录/注册表 + 种子 + 环境变量覆盖。
3. API key 加密存储。
4. 权限/可见性过滤（参考 Open WebUI get_filtered_models + RBAC）。
5. 下拉框/能力位元数据（参考 open-canvas ALL_MODELS 含 temperatureRange/maxTokens）。
6. provider 名→本地配置路由表。
7. 多消费方声明式引用（主对话/标题/推荐/转写/记忆 各声明 model_id/role，模块按 id 查卡片→构造）。
8. 安全：显式枚举 configurable_fields 防注入。

## 借鉴蓝本

- **LobeChat**（最完整）：ChatModelCard + model-bank(DEFAULT_MODEL_PROVIDER_LIST) + LobeRuntimeAI 统一接口 + runtimeMap + createOpenAICompatibleRuntime 工厂 + useAiInfraStore + OPENAI_MODEL_LIST 覆盖语法。消费方只持 model id，不持 key/base_url。
- **Open WebUI**（后端集中+RBAC）：app.state.config + OPENAI_API_BASE_URLS/CONFIGS + get_all_models_responses + get_filtered_models 按 AccessGrants/Groups 过滤。
- **open-canvas**（最贴 LangGraph 官方）：getModelConfig(按前缀推断 provider+env 取 key/base) + getModelFromConfig(initChatModel) + ALL_MODELS 卡片。

## 边界一句话

生态给「构造+注入」两层；项目要自己造「管理」层（卡片/存储/加密/权限/下拉/路由表/声明式引用）。LambChat 方向与生态共识一致，不用重写，是把 LLMClient 工厂升格为管理模块 + 收编旁路消费方。

## 关键来源

- LangChain init_chat_model: https://reference.langchain.com/python/langchain/chat_models/base/init_chat_model
- LangGraph configurable: https://langchain-ai.github.io/langgraph/how-tos/configuration/
- open-canvas 源码: https://github.com/langchain-ai/open-canvas (apps/agents/src/utils.ts, packages/shared/src/models.ts)
- deepagents: https://docs.langchain.com/oss/python/deepagents/models
- LobeChat: https://deepwiki.com/lobehub/lobe-chat
- Open WebUI: https://deepwiki.com/open-webui/open-webui
