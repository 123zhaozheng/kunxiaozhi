# Phoenix + LangSmith tracing provider selector

## Goal

在现有 LangSmith 追踪之外接入自托管 Arize Phoenix；管理端用 **下拉框** 选择唯一生效的 tracing 后端（互斥），使 LangGraph / DeepAgents 的 LLM / 工具 / graph 导出到所选后端。

## Background (confirmed)

- Agent 栈：LangGraph + `deepagents` + LangChain chat models（`src/infra/llm/client.py`）。
- 已有 `LANGSMITH_*`（`SettingCategory.TRACING` / `langsmith`）与 `src/infra/tracing/*`。
- 真实 LangSmith 路径主要是 **env 同步 + LangGraph 自动追踪**；`tracer` / `@traced` / `get_langsmith_url` 几乎无生产调用。
- 本地 Phoenix：`deploy/docker-compose.phoenix.yml`，UI `:6006`，OTLP HTTP `/v1/traces`。
- 管理端 SELECT 模式对齐 `SANDBOX_PLATFORM`；`depends_on` 对非 boolean 父项用 `{"key","value"}`。
- OTEL instrumentor / TracerProvider **不能热拔插**；provider 变更需进程重启。

## Requirements

### R1. Provider 下拉（互斥）

- 新设置 `TRACING_PROVIDER`：`SettingType.SELECT`，`options: ["none", "langsmith", "phoenix"]`，默认 `none`。
- 管理端 Tracing 区展示下拉；按 provider 显示 LangSmith / Phoenix 子配置（`depends_on`）。
- **不实现 `both`**（同次 run 双写）。后续若需要再开任务。

### R2. LangSmith provider

- 保留 LangSmith 的凭据、项目、URL、采样率字段；当 provider=`langsmith` 时启用 LangSmith。
- `TRACING_PROVIDER` 是唯一配置真源；删除 `LANGSMITH_TRACING` 设置及 legacy 映射。
- 当 provider 为 `none` 或 `phoenix` 时，必须把 `LANGSMITH_TRACING` **强制写入 env 为 false**（修复今日“仅 true 时写入、false 不清理”的问题）。

### R3. Phoenix 接入

- 新设置（subcategory `phoenix`）：
  - `PHOENIX_COLLECTOR_ENDPOINT`（默认 `http://localhost:6006/v1/traces`）
  - `PHOENIX_PROJECT_NAME`（默认 `lamb-agent`）
  - 可选：`PHOENIX_API_KEY`（本地 compose 可空）
- 依赖：`arize-phoenix-otel>=0.16.0`、`openinference-instrumentation-langchain`（uv）。
- 启动时：`phoenix.otel.register(project_name=…, endpoint=…, auto_instrument=True, batch=True, protocol="http/protobuf")`。
- 进程级 once-guard；shutdown 时 `tracer_provider.shutdown()`。
- Phoenix 不可达不得拖垮 Agent 主路径（register 失败记日志并降级）。

### R4. 配置与运维

- `TRACING_PROVIDER` 及 Phoenix 核心字段加入 `RESTART_REQUIRED_SETTINGS`（或等价 UI 提示）。
- `.env.example` 同步。
- 不改 `BaseGraphAgent` / 各 agent graph 业务流；不改 HTTP `middleware/tracing.py`。
- 不替换项目内部 Presenter / DualWriter / Mongo trace。

## Acceptance Criteria

- [ ] AC1：管理端 Tracing 区有 `TRACING_PROVIDER` 下拉（none/langsmith/phoenix），子字段随选择显隐。
- [ ] AC2：`provider=langsmith` 时既有 LangSmith 导出路径可用（回归）。
- [ ] AC3：`provider=phoenix` + 本地 Phoenix 运行时，跑一轮 Agent 后 Phoenix UI 可见 spans。
- [ ] AC4：`provider=none` 时不向 LangSmith/Phoenix 导出（env 强制关 LangSmith；不 register Phoenix）。
- [ ] AC5：`provider=phoenix` 时不向 LangSmith 导出（env 强制 false）。
- [ ] AC6：uv 依赖与 `.env.example` / definitions / i18n（en+zh 至少，现有五语言补齐）同步。
- [ ] AC7：单元测试覆盖 provider 解析、非法值禁用、env apply、phoenix init 守卫（mock）。

## Out of scope

- `both` 双写；Phoenix evals/数据集；per-user project 路由；前端 SSE 暴露 Phoenix URL。

## Decisions

| 项 | 决定 |
|---|---|
| Provider 集合 | `none` \| `langsmith` \| `phoenix`（互斥） |
| both | 不做 |
| 热切换 | 需重启进程 |
| 默认 | `none` |
| endpoint | HTTP `/v1/traces` |
