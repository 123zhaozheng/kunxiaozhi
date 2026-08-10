# 完善 Team 模式成员与团队准入

## Goal

以最小前端改动保证 Team 模式只能使用已经构建并明确选中的团队，同时避免用户在组建 Team 时选择 Fast Persona。

## Background

- Team 模式已经使用独立 harness 和 DAG，本次不调整其运行机制。
- 聊天允许用户在 Fast、Search、Team 三种模式间切换，但当前 Team 模式未选择团队时仍可发送并进入后端 fallback。
- 现有 `TeamPickerModal` 已支持团队列表、选择、空状态、新建和管理入口，可直接复用。
- Team 成员只引用 Persona；Persona 的 `preferred_agent_id` 表示 Fast/Search 偏好，Team 成员没有独立模式字段。
- 项目尚未上线，不需要历史配置迁移或兼容处理。

## Requirements

### R1. Team 组建候选仅包含 Search Persona

- 新建或编辑 Team 时，成员候选列表只展示 `preferred_agent_id === "search"` 的 Persona。
- Fast Persona 不可在 TeamBuilder 中被选择为成员。
- 不新增 Team 成员模式字段，不增加后端或运行时重复校验。

### R2. Team 聊天必须选择已有团队

- 用户切换到 Team 模式且尚未选择团队时，自动打开现有团队选择器。
- 未选择团队时，Team 模式不能发送消息。
- 存在团队时，用户可在现有选择器中选择一个已经构建好的团队，选择后恢复发送能力。
- 不存在团队时，选择器显示现有空状态，并提供可直接进入团队创建流程的操作入口。
- 已经选中过团队时允许继续沿用该选择，不要求每次切换模式都重复选择。
- Fast 和 Search 模式的切换与发送行为保持不变。

## Acceptance Criteria

- [ ] TeamBuilder 只展示 Search Persona，Fast Persona 不出现在成员候选列表中。
- [ ] 切换到 Team 且 `selectedTeamId` 为空时，团队选择器自动打开。
- [ ] Team 模式在 `selectedTeamId` 为空时无法提交消息。
- [ ] 选择已有团队后，Team 模式能够正常提交消息并沿用现有 `team_id` 请求逻辑。
- [ ] 没有团队时，用户能从选择器空状态进入现有团队创建页面。
- [ ] 已选团队在模式往返切换后仍可复用。
- [ ] Fast、Search 模式的发送行为不受影响。
- [ ] 关键状态具备前端自动化测试，替换当前“Team 无选择也可提交”的旧断言。

## Key Decisions

- 采用前端最小改动，不修改后端无 `team_id` fallback 契约。
- 不修改 Team harness、DAG 或角色子代理运行时。
- 不处理 Persona 加入 Team 后再被改为 Fast 的漂移场景。
- 不新增历史迁移、运行时纠正或兼容分支。

## Out Of Scope

- Team API、存储模型和后端校验变更。
- Team harness、DAG、工具或提示词调整。
- 已有 Team 数据迁移或清理。
- Persona 后续模式变更与既有 Team 的联动约束。
