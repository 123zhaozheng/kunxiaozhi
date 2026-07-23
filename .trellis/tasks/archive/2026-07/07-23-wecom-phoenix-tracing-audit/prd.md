# 确认企微消息是否进入 Phoenix 追踪

## Goal

确认企业微信入站消息触发的 Agent 执行是否进入项目统一追踪链路，并在启用 Phoenix 时生成可查询的 trace/span；识别与 Web 会话相比缺失或不同的追踪字段。

## Requirements

- 追踪企微消息从 SDK handler、任务提交、worker 执行到 tracing exporter 的完整调用链。
- 确认独立 `wecom-runtime`、API 与 ARQ worker 各自是否初始化 Phoenix/OpenTelemetry。
- 区分“企微传输层 WebSocket 活动”和“企微消息触发的 Agent/LLM 执行”是否被追踪。
- 确认 trace、session、user、persona、run 等关键关联字段是否持久化或上报。
- 给出明确结论、成立条件、验证方式和已知缺口。

## Acceptance Criteria

- [x] 给出带代码位置证据的调用链。
- [x] 明确哪些企微活动会进入 Phoenix、哪些不会。
- [x] 明确独立 runtime 模式下 Phoenix 初始化是否充分。
- [x] 提供可执行的运行时验证步骤。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
