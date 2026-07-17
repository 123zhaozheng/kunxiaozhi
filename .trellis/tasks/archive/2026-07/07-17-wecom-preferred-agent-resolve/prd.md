# 企微 handler 统一 resolve preferred_agent

## Goal

企微消息处理去掉硬编码 `agent_to_use = "search"`，改为与 Web 同一 `resolve_persona_agent_id(preset)`；会话写入 `agent_id` + `persona_preset_id`。

## Requirements

- `create_wecom_message_handler`（及同类路径）使用 resolve，禁止写死 search/team。
- preset 缺失/无效 → fast。
- 不重做 wecom bot CRUD / reload_preset 配置 UI。
- 与 `07-17-preferred-agent-binding` 共享 resolve 实现（可先实现函数再接线）。

## Acceptance Criteria

- [ ] 代码中 wecom 消息路径无 `agent_to_use = "search"` 常量赋值。
- [ ] preset.preferred_agent_id=team 时企微路径选用 team（单测或可测分支）。
- [ ] 企微产生的会话带 persona_preset_id 与解析后的 agent_id。

## Dependencies

- 理想：binding 已提供字段与 resolve。
- 可并行：先引入 resolve(缺省 fast)，binding 合入后自动读字段。

## Notes

- 父任务：`07-17-persona-template-and-analytics`
