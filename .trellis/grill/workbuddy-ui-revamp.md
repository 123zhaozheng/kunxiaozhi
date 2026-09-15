# Grill: WorkBuddy 风格 UI 改造

Date: 2026-09-15

## Intent

让昆小智对公司内部小白用户"一眼会用"：把三个技术味的 Agent 模式
（Search/Fast/Team Agent）换成任务语义的中文模式，并把整体视觉从
米白 + 彩虹卡片头，改成 WorkBuddy 式的白底内容区 + 灰侧栏 + 干净卡片。
仿照而非照抄 WorkBuddy。

## Constraints

- 内网离线：不能引外部图片 / CDN / 字体 / 新的重型依赖。
- 后端 agent ID `fast` / `search` / `team` 不能改（注册表 + 路由契约）。
- 现有切换逻辑必须保持："切换之后的逻辑按照原来的没有问题"。
- 三个模式必须仍可在管理端配置（已有 `/api/agent/config/catalog`）。
- 暗色模式与移动端不能回归。

## Key decisions

- 决策：模式改名只做展示层映射，不动运行时 ID。
  原因：后端 `@register_agent("fast"|"search"|"team")` 与
  `/api/chat/stream?agent_id=` 是稳定契约；改 ID 会波及会话历史与统计。
  备选（已否决）：重命名后端 agent 目录与 ID。

- 决策：中文名落在 i18n `agents.*.name`，而非硬编码组件文案。
  原因：管理端 catalog `labels` 已有覆盖机制（`agentCatalog.ts:23-37`），
  改 i18n 可同时让管理端默认值与前台一致。

- 决策：首页模式 pills 复用 `/api/agents` 返回的可见列表，而不是写死三个。
  原因：管理端可禁用某模式、按角色授权；写死会让被禁用的模式仍然显示。

- 决策：保留"更多"菜单，仅把 技能 / MCP 提升进"专家·技能·连接器"合并页。
  原因：记忆（/memory）不属于这三类，仍需入口。

## Surfaced assumptions（已通过代码验证）

- "我能为你做什么"来自 `welcomeSubtitle`（zh.json:881-883），删除它安全。
- 思考强度后端已支持 off/low/medium/high/max，做滑杆是纯前端改造。
- 首页目前没有推荐卡 / 换一批，需要新建而非改造。
- 角色卡彩虹头来自共享 `SkillBaseCard`，一处改动同时影响角色与技能卡。

## Open questions

见与用户确认的 8 个问题（首轮）。

## Out of scope

- WorkBuddy 的积分、会员、活动弹窗、发现应用。
- 输入框上方的能力 chips（文档处理/金融服务/幻灯片…）——用户明确不要。
- 顶部"做任务赢积分好礼"类运营位。
