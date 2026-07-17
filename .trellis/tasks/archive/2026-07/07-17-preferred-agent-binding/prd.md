# preferred_agent 绑定（schema/API/广场/编辑器）

## Goal

为 Persona 增加 `preferred_agent_id`（默认 fast，可选 search/team）；广场「使用」走绑定而非硬编码 team；编辑表单可配置；会话内只读；新会话持久化 `agent_id` + `persona_preset_id`。

## Requirements

- PersonaPreset schema/API：字段 `preferred_agent_id: fast|search|team`，默认 fast。
- 读旧数据缺字段 → 解析为 fast，不回写 DB。
- 共享 `resolve_persona_agent_id`（后端权威；前端展示/选用一致）。
- 前端：删除/改写 `resolvePersonaAgentId` 一律 team 逻辑。
- 编辑/创建表单：能力模板选择器。
- 广场卡片：只读徽章。
- 使用 Persona 开聊：agent = preferred；写入 session 双字段。
- 带 persona 的会话：服务端或前端保证不可改模板（与父 PRD R1.5 一致）。

## Acceptance Criteria

- [ ] Create persona 不传 preferred → 存/读为 fast。
- [ ] Update preferred=team → Get 返回 team。
- [ ] 广场使用 preferred=search 的 persona → 会话 agent_id=search 且有 persona_preset_id。
- [ ] 前端无「persona → 强制 team」分支。
- [ ] 卡片展示对应徽章。

## Dependencies

- 无。企微子任务会消费本任务的 resolve。

## Notes

- 父任务：`07-17-persona-template-and-analytics`
- 设计见父 `design.md` §2–§4.1
