# Technical Design: Single Chinese Agent Harness

## Architecture

### Canonical authority

保留 `src/agents/core/harness_prompt_overrides.py` 作为唯一 Harness authority，负责：

- 默认中文简洁 behavior guide 与 Harness catalog；
- 模型视图工具/schema 本地化；
- DeepAgents vendor prompt replacement；
- provider profile 构造与显式幂等注册；
- Todo 工具名、系统段标题等共享常量。

`persona.py` 仅处理 persona 相关内容，不再通过 import side effect 注册 Harness。

### Single Todo path

默认 provider profile：

```text
DeepAgents TodoListMiddleware (唯一运行时实现)
  -> 追加 vendor Todo prompt/tool
  -> HarnessLocalizationMiddleware
       - 替换 vendor Todo prompt 为默认中文简洁文本
       - 复制并本地化 write_todos 模型视图 schema
  -> model request
```

共享 profile 不再排除 `TodoListMiddleware`，不再构造 `ShortTodoListMiddleware`，`extra_middleware` 只包含模型视图本地化层及未来明确的共享 middleware。

### Team capability override

Team 显式团队路径在同步 `create_deep_agent()` 组装区间安装 model-specific overlay：

- `excluded_middleware={TodoListMiddleware}`；
- 不认识任何 ShortTodo 类型；
- 保留 `TeamToolExclusionMiddleware` 作为 request fallback，并从 core Harness authority 导入统一常量。

Fast/Search 只解析默认 provider profile，因此保留原生 Todo middleware；Team overlay 在完整栈排除阶段移除唯一 Todo middleware。

## Registration contract

- 提供显式、幂等的 `ensure_default_harness_registered()`；由 Agent core bootstrap 或三个 node 模块的公共导入路径调用。
- 注册 provider keys：`anthropic`、`openai`、`google_genai`，沿用当前解析行为。
- 不提供 `compact_zh` 旧名兼容别名。
- Team model-specific overlay 进入前保存原 registry entry，退出或异常时精确恢复；key 无法解析时不改 registry。

## Compatibility

- runtime BaseTool/Pydantic schema 保持原对象；本地化只复制模型视图。
- `task` 使用渲染后的动态描述；本地化不得覆盖实际 agent list。
- 现有 `todo:updated` presenter/前端回放协议不变。
- Team 的无团队 fallback 行为以现有产品路径为准，由实现前测试固化。

## Risks and mitigations

- Middleware 顺序变化导致 vendor Todo 文案未被替换：增加真实 model-request 快照测试，而非只检查 profile 元数据。
- Team role subagent 仍出现 Todo：增加声明式 role subagent 最终请求回归测试。
- provider 注册依赖导入顺序：保留 fresh-process bootstrap 与 direct-first infra import 测试。
- DeepAgents 升级改变 vendor prompt：保留 SHA-256 pin，失败时显式审查替换边界。
- 全局 registry 泄漏：测试嵌套、异常和 unresolved key 恢复行为。

## Rollback

本改动应形成单一可回滚提交。回滚后恢复当前 provider profile + ShortTodo + Team 双精确类型排除结构；不涉及数据迁移或持久化格式变化。

