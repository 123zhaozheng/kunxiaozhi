# 统一中文 Agent Harness 配置

## Goal

将当前被命名为 `compact_zh` 的内部实现收敛为项目唯一的中文简洁 Agent Harness，消除“原生 Todo middleware 被排除后再由 ShortTodo 子类加回”的双层结构。FastAgent 与 SearchAgent 保留一个权威的 Todo 能力；TeamAgent 因 SOP 替代 Todo 而明确禁用该能力。

## Background

- 当前 `src/agents/core/persona.py` 通过导入副作用注册所谓 `compact_zh` provider profile。
- 共享 profile 排除 DeepAgents 原生 `TodoListMiddleware`，同时通过 `extra_middleware` 加入自定义 `ShortTodoListMiddleware`。
- DeepAgents 0.6.7 的 middleware 排除按精确类型匹配，导致 Team 必须同时识别父类与子类，曾出现工具已过滤但 Todo 系统提示仍被重新注入的问题。
- 仓库检索仅发现内部源码、测试和 Trellis 文档使用 `compact_zh` 命名，没有需要保留的外部公开 API 调用者。
- DeepAgents 原生 Todo middleware 与项目 `HarnessLocalizationMiddleware` 已具备实现单一 Todo 的基础：保留原生 middleware，由后置本地化层改写最终模型可见的提示与工具描述。

## Requirements

### R1 单一 Harness 权威

- 项目只有一套默认中文简洁 Harness，不再存在可选或并列的 `compact_zh` 模式概念。
- Harness catalog、provider 注册、模型视图本地化、公共常量和 profile 构造归属同一个 core authority；`persona.py` 不再承担 Harness 注册副作用。
- 删除内部 `compact_zh`、`COMPACT_ZH_*`、`ShortTodoListMiddleware` 等旧概念和命名，不保留行为兼容别名。

### R2 单一 Todo 实现

- FastAgent 与 SearchAgent 只使用 DeepAgents/ LangChain 的一个 `TodoListMiddleware` 实例，不再排除后用子类重新添加。
- `HarnessLocalizationMiddleware` 在最终模型请求中把 Todo 系统提示和 `write_todos` 工具描述替换为项目唯一的中文简洁版本。
- Todo 契约继续要求恰好一个 `in_progress`，且禁止并行调用 Todo 工具。

### R3 Agent 能力差异

- FastAgent、SearchAgent：启用 Todo；最终模型可见 `write_todos` 和中文简洁指导。
- TeamAgent 显式团队路径：由 SOP 替代 Todo；主代理和角色子代理最终请求均不含 `write_todos` 工具或 Todo 系统提示。
- Team 的 request-layer exclusion 保留为 unresolved model/profile 失败和依赖升级的防御性兜底，但消费统一的工具名与标题常量，不再复制字符串。
- Team 无团队 fallback 的既有产品行为保持不变，并由测试明确锁定其 Todo 策略。

### R4 运行时与 Schema 兼容

- 工具名、字段名、required、default、enum、type 与运行时 Pydantic 校验完全不变；只允许改变模型可见的 `description`/`title`。
- 已渲染的 `task` 工具描述必须保留真实 agent 列表，不得恢复 `{available_agents}` 占位符。
- 未知、延迟加载或不可序列化 schema 的工具继续原样透传。
- OpenAI、Anthropic、Google GenAI 当前 provider profile 解析保持有效；无法解析模型 key 时不得污染全局 registry。

### R5 规范与命名同步

- 更新 `.trellis/spec/backend/agent-harness.md`，改为“唯一默认中文简洁 Harness”契约，移除 `compact_zh` 模式表述。
- 更新相关源码注释、日志和测试名称，避免继续暗示存在两套 Harness。

## Acceptance Criteria

- [ ] 全仓库非归档业务源码与测试中不再出现 `compact_zh`、`COMPACT_ZH`、`ShortTodoListMiddleware` 等旧模式/替代层标识。
- [ ] FastAgent 与 SearchAgent 的最终模型请求各只有一个 Todo middleware 行为，包含 `write_todos` 和中文简洁 Todo 指导。
- [ ] TeamAgent 主代理与声明式角色子代理的最终模型请求均不含 `write_todos` 或 Todo 系统提示，SOP/`task` 等其他工具和提示不受影响。
- [ ] Todo 的 one-in-progress/no-parallel 契约、`todo:updated` 事件兼容和历史事件处理保持有效。
- [ ] 三个 provider 的默认 profile 解析、fresh-process 启动、动态 `task` agent 列表、schema 注解等价、未知工具透传测试通过。
- [ ] Team profile 的安装/恢复、异常路径、unresolved key fallback 和 request-layer 兜底测试通过。
- [ ] 相关 backend tests、ruff 和类型检查通过。

## Out of Scope

- 不改变 Fast/Search/Team 的业务职责、系统提示内容含义或工具权限，除 Todo/Harness 统一所需改动外不做大范围提示词重写。
- 不升级 DeepAgents/LangChain 依赖版本。
- 不重构 SOP 状态机、审批门禁、DAG UI 或 Team 调度策略。
- 不修改工具运行时 schema、API 契约或前端消息格式。

## Constraints

- DeepAgents 0.6.7 没有公开的 per-call `HarnessProfile` 参数；Team 的能力覆盖仍需使用同步 registry context 或 request fallback。
- profile registry 是全局可变状态，任何安装/恢复区间不得跨越 `await`。
- `CompiledSubAgent` 不自动继承 profile extra middleware；不得声称本次重构覆盖未显式装载本地化 middleware 的外部编译图。

