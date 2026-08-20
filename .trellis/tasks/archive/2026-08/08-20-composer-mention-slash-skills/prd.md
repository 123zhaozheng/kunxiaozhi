# 输入框 @ 角色修复与 / 技能选择

## Goal

修好输入框 `@` 选角色，并补上最简单的 `/` 技能强调：从已经注入上下文的个人空间技能里筛选、多选，芯片一直留在输入框上方直到用户关掉；发送时只在提示词里强调必须使用这些技能，不改变技能注入集合。

## Background

用户报告 `@` 提示在、选择却失效；希望 `/` 像外网产品一样打开技能列表（现在只有 `/goal`），`/pe` 过滤，可多选，选中后用芯片展示。个人空间已启用技能本来就会注入上下文；`/` 只是强调要用哪几个，其余技能仍在。芯片发送后保持，不自动清掉。

## Confirmed facts

- Placeholder：`frontend/src/i18n/locales/zh.json:757`「@ 可切换角色」。
- `@` 检测：`frontend/src/hooks/useMentionState.ts:20-41`，`@` 必须在词首。
- 欢迎页未选角色时传入 `onMentionQueryChange`，`ChatInput.tsx:671-687` 因此不渲染 `MentionPopup`，只筛下方卡片。这是 `@` 失效的首要代码嫌疑。
- 已有会话或已选角色后才走 `MentionPopup`。
- `/` 菜单只硬编码 `/goal`：`frontend/src/components/chat/chatInputSlashCommands.ts:8-15`。查询必须从输入开头开始、中间不能有空格。
- 已启用技能经 `enabled_skills` 进入 `SkillsStoreBackend`（`src/infra/backend/skills_store.py:188-193`）。`/` 不改这个集合。
- 可复用：`skillMatchesQuery`、`ToolbarChip`、`ActiveGoalBar`、现有 slash 浮层。
- 欢迎页和消息列表各挂一个 `ChatInput`。芯片若只存在组件内部，发出第一条消息后会丢。

## Requirements

- R1. `@` 在欢迎页未选角色、已选角色、已有会话三种场景都能选出角色（团队模式选出团队），与 placeholder 一致。欢迎页未选角色时也要弹出 mention 列表，不能只靠筛卡片。
- R2. 输入框开头输入 `/` 打开当前已注入技能列表；`/pe` 按名称、描述、标签过滤。未启用技能不出现。
- R3. 技能可多选。选中后清掉当前 `/query`，在输入框上方显示可关闭芯片，正文不留 `/技能名`。
- R4. 发给模型的内容仍带强调提示词（例如「请必须使用 xx 技能」）。用户气泡里不显示这句；用胶囊 UI 展示蓝色技能名 + 用户原文。复制只复制用户原文。未选中的已注入技能保持原样。不改 `enabled_skills`。
- R5. `/goal` 仍在斜杠菜单里，行为不变。
- R6. 方向键高亮，Enter/Tab 选中，Esc 关闭；中文 IME 组合期间不误选。
- R7. 芯片画在输入框内部（占位符上方），发送成功后从输入框清掉。沙箱容量失败时还原芯片。

## Out of scope

- 重做技能面板 / 角色面板。
- 通用命令面板（设置、主题、工具）。
- 技能创建、安装、市场。
- 改变默认技能注入，或用 `/` 增删本轮可见技能。
- 后端新协议；芯片跨会话持久化。

## Acceptance Criteria

- [ ] AC1. 欢迎页未选角色时输入 `@`，出现角色浮层并可完成选择。
- [ ] AC2. 已选角色或已有会话时输入 `@`，出现角色浮层并可切换。
- [ ] AC3. `/` 列出当前已注入技能；`/pe` 只留下名称/描述/标签含 `pe` 的项。
- [ ] AC4. 可连续多选；芯片在输入框上方，可单独移除。
- [ ] AC5. 发给模型的内容带强制使用提示词；气泡不展示该提示词，只展示技能胶囊 + 用户原文；复制得到用户原文；`enabled_skills` 与未强调时相同。
- [ ] AC6. `/goal` 仍能选中并进入目标模式。
- [ ] AC7. 无已注入技能、无匹配、Esc、点外部时列表关闭。
- [ ] AC8. 芯片在输入框内部、占位符上方；发送成功后输入框里不再留芯片；点关闭仍可去掉强调。

## Technical Notes

- 前端-only。强调文本在 `ChatInput` 提交时拼进 `onSend` 的 content。气泡用 `parseEmphasizedUserMessage` 拆出技能名和可见正文。
- 芯片状态放到 `ChatView`，经 `chatInputProps` 同时喂给欢迎页和底部输入框。
- 斜杠菜单沿用现有 drop-up，技能项和 `/goal` 同一列表。
- 调研：`.trellis/tasks/08-20-composer-mention-slash-skills/research/mature-slash-skill-picker.md`。
